import logging
from langchain_anthropic import ChatAnthropic
from app.config import get_settings
from typing import Annotated, Literal, TypedDict
from operator import add
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END

logger = logging.getLogger("pr-reviewer.agents")
MODEL = "claude-sonnet-5"        # balance of cost/quality; haiku=cheaper, opus=sharper
# Verify against current Anthropic pricing — these are $ per million tokens.
PRICE_INPUT_PER_M = 2.00
PRICE_OUTPUT_PER_M = 10.00

class Finding(BaseModel):
    file: str = Field(description="Path of the file the issue is in")
    line: int | None = Field(default=None, description="Line number if identifiable")
    category: Literal["security", "quality", "testing", "docs"] = Field(
        description="Which domain this finding belongs to"
    )
    severity: Literal["low", "medium", "high"] = Field(description="How serious the issue is")
    confidence: float = Field(ge=0.0, le=1.0, description="How sure the agent is (0–1)")
    message: str = Field(description="What the issue is and why it matters")

class PRState(TypedDict):
    context: dict
    findings: Annotated[list[Finding], add]
    # aggregator outputs (written once, so no reducer):
    usage: Annotated[list[dict], add] # one record per LLM call
    high_findings: list[Finding]
    low_findings: list[Finding]
    summary: str

class AgentResponse(BaseModel):
    findings: list[Finding]      # the wrapper — what Claude actually fills in

def compute_cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * PRICE_INPUT_PER_M
            + output_tokens * PRICE_OUTPUT_PER_M) / 1_000_000

def _format_diff(context: dict) -> str:
    parts = [
        f"PR title: {context.get('title', '')}",
        f"Description: {context.get('description') or '(none)'}",
        "",
        "Changed files and diffs:",
    ]
    for f in context.get("files", []):
        parts.append(
            f"\n--- {f['filename']} ({f['status']}, "
            f"+{f['additions']} -{f['deletions']}) ---"
        )
        parts.append(f.get("patch") or "(no textual diff available)")
    return "\n".join(parts)

def _extract_usage(node: str, raw) -> dict:
    """Pull token counts off a raw AIMessage; degrade gracefully if absent."""
    meta = getattr(raw, "usage_metadata", None) or {}
    return {
        "node": node,
        "input_tokens": meta.get("input_tokens", 0),
        "output_tokens": meta.get("output_tokens", 0),
    }

_CONFIDENCE_RUBRIC = """

Set `confidence` honestly: use values near 1.0 only when you can point to the exact line and are certain; use lower values when you suspect an issue but cannot fully verify it from the diff alone. Never inflate confidence. If the diff shows no issues in your domain, return an empty findings list — a clean result is a valid, useful answer."""

SECURITY_PROMPT = """You are a security-focused reviewer examining a GitHub pull request diff.
Report ONLY genuine security concerns: injection, hardcoded secrets or credentials, unsafe deserialization, missing auth/authorization, path traversal, unsafe handling of user input, or weak cryptography. Ignore style, tests, and docs — other reviewers cover those.""" + _CONFIDENCE_RUBRIC

QUALITY_PROMPT = """You are a code-quality reviewer examining a GitHub pull request diff.
Report ONLY maintainability and correctness concerns: logic bugs, unhandled errors or edge cases, resource leaks, confusing or misleading names, dead code, needless complexity, or copy-paste duplication. Ignore security, tests, and docs.""" + _CONFIDENCE_RUBRIC

TESTING_PROMPT = """You are a testing reviewer examining a GitHub pull request diff.
Report ONLY testing gaps: new or changed logic left untested, missing edge-case or error-path tests, assertions that don't actually verify behavior, or tests that were removed or weakened. Ignore security, general quality, and docs.""" + _CONFIDENCE_RUBRIC

DOCS_PROMPT = """You are a documentation reviewer examining a GitHub pull request diff.
Report ONLY documentation concerns: public functions, classes, or APIs changed without updated docstrings; comments that are now misleading or inaccurate; or user-facing changes not reflected in the README or docs. Ignore security, quality, and tests.""" + _CONFIDENCE_RUBRIC

def make_agent(name: str, category: str, system_prompt: str):
    """Build an agent node: same machinery, different prompt and category."""

    async def agent(state: PRState) -> dict:
        context = state["context"]
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set — agents need it to call Claude."
            )

        model = ChatAnthropic(
            model=MODEL,
            api_key=settings.anthropic_api_key,
            max_tokens=2000,
        ).with_structured_output(AgentResponse, include_raw=True)

        logger.info("%s agent reviewing %s#%s", name, context["repo"], context["pr_number"])
        response = await model.ainvoke([
            ("system", system_prompt),
            ("human", _format_diff(context)),
        ])

        parsed = response["parsed"]
        usage = _extract_usage(name, response["raw"])

        # Force-stamp the domain — don't trust the model to label its own category.
        findings = [f.model_copy(update={"category": category}) for f in parsed.findings]
        logger.info("%s agent found %d issue(s)", name, len(findings))
        return {"findings": findings, "usage": [usage]}

    agent.__name__ = f"{name}_agent"      # nicer name in logs / traces
    return agent


security_agent = make_agent("security", "security", SECURITY_PROMPT)
quality_agent = make_agent("quality", "quality", QUALITY_PROMPT)
testing_agent = make_agent("testing", "testing", TESTING_PROMPT)
docs_agent = make_agent("docs", "docs", DOCS_PROMPT)

HIGH_CONFIDENCE = 0.7
NOISE_FLOOR = 0.3


def _dedupe(findings: list[Finding]) -> list[Finding]:
    """Collapse findings that point at the same file+line+category."""
    seen, unique = set(), []
    for f in findings:
        key = (f.file, f.line, f.category)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def _sort_key(f: Finding):
    order = {"high": 0, "medium": 1, "low": 2}
    return (order[f.severity], -f.confidence)   # severe first, then most confident

SUMMARY_PROMPT = """You are the lead reviewer writing a short summary of a PR review.
You are given findings already triaged by specialist agents. Write 2–4 sentences for the PR author: lead with the most important issues, be direct and specific, and don't invent problems not in the findings. If there are no high-confidence findings, say the PR looks clean and note any minor points briefly."""

def _render_findings(high: list[Finding], low: list[Finding]) -> str:
    lines = ["High-confidence findings:"]
    lines += [f"- [{f.severity}] {f.file}: {f.message}" for f in high] or ["- (none)"]
    lines.append("\nLow-confidence (demoted) findings:")
    lines += [f"- [{f.severity}] {f.file}: {f.message}" for f in low] or ["- (none)"]
    return "\n".join(lines)

async def _summarize(context, high: list[Finding], low: list[Finding]) -> tuple[str, dict]:
    settings = get_settings()
    if not settings.anthropic_api_key:
        return "(summary unavailable — no API key)", _extract_usage("aggregate", None)
    try:
        model = ChatAnthropic(model=MODEL, api_key=settings.anthropic_api_key, max_tokens=500)
        resp = await model.ainvoke([
            ("system", SUMMARY_PROMPT),
            ("human", f"PR: {context.get('title','')}\n\n{_render_findings(high, low)}"),
        ])
        return resp.content, _extract_usage("aggregate", resp)
    except Exception as e:
        logger.warning("Summary generation failed: %s", e)
        return "(summary generation failed)", _extract_usage("aggregate", None)
    
async def aggregator(state: PRState) -> dict:
    findings = _dedupe(state["findings"])

    high = sorted([f for f in findings if f.confidence >= HIGH_CONFIDENCE], key=_sort_key)
    low = sorted(
        [f for f in findings if NOISE_FLOOR <= f.confidence < HIGH_CONFIDENCE],
        key=_sort_key,
    )
    # below NOISE_FLOOR is dropped entirely
    dropped = len(findings) - len(high) - len(low)

    logger.info(
        "Aggregated: %d high, %d low-confidence, %d dropped as noise",
        len(high), len(low), dropped,
    )

    summary, summary_usage = await _summarize(state["context"], high, low)
    return {
        "high_findings": high,
        "low_findings": low,
        "summary": summary,
        "usage": [summary_usage],
    }
    
def build_review_graph():
    builder = StateGraph(PRState)

    agents = {
        "security": security_agent,
        "quality": quality_agent,
        "testing": testing_agent,
        "docs": docs_agent,
    }
    for name, node in agents.items():
        builder.add_node(name, node)
        builder.add_edge(START, name)          # fan-out
        builder.add_edge(name, "aggregate")    # fan-in → converge on aggregator

    builder.add_node("aggregate", aggregator)
    builder.add_edge("aggregate", END)

    return builder.compile()


review_graph = build_review_graph()