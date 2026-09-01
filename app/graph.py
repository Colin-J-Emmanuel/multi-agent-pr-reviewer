import logging
from langchain_anthropic import ChatAnthropic
from app.config import get_settings
from typing import Annotated, Literal, TypedDict
from operator import add
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END

logger = logging.getLogger("pr-reviewer.agents")
MODEL = "claude-sonnet-5"        # balance of cost/quality; haiku=cheaper, opus=sharper

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
    context: dict                              # the PR data from GitHubClient.fetch_pr()
    findings: Annotated[list[Finding], add]    # agents append here; reducer keeps all

class AgentResponse(BaseModel):
    findings: list[Finding]      # the wrapper — what Claude actually fills in

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

SECURITY_SYSTEM_PROMPT = """You are a security-focused code reviewer examining a GitHub pull request diff.

Report ONLY genuine security concerns: injection risks, hardcoded secrets or credentials, unsafe deserialization, missing auth/authorization checks, path traversal, unsafe handling of user input, or weak/insecure cryptography. Do NOT report style, formatting, or non-security bugs — other reviewers cover those.

Set `confidence` honestly. Use values near 1.0 only when you can point to the exact vulnerable line and are certain; use lower values when you suspect an issue but cannot fully verify it from the diff alone. Never inflate confidence.

If the diff shows no security issues, return an empty findings list — a clean result is a valid, useful answer. Every finding you return must use category "security"."""

async def security_agent(state: PRState) -> dict:
    context = state["context"]
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set — the security agent needs it to call Claude."
        )

    model = ChatAnthropic(
        model=MODEL,
        api_key=settings.anthropic_api_key,
        max_tokens=2000,
    ).with_structured_output(AgentResponse)

    logger.info("Security agent reviewing %s#%s", context["repo"], context["pr_number"])
    response = await model.ainvoke([
        ("system", SECURITY_SYSTEM_PROMPT),
        ("human", _format_diff(context)),
    ])

    # Safety by construction: this is the security agent, so its findings are security —
    # don't trust the model to always label its own domain correctly.
    findings = [f.model_copy(update={"category": "security"}) for f in response.findings]
    logger.info("Security agent found %d issue(s)", len(findings))
    return {"findings": findings}

def build_review_graph():
    builder = StateGraph(PRState)
    builder.add_node("security", security_agent)
    builder.add_edge(START, "security")
    builder.add_edge("security", END)
    return builder.compile()


review_graph = build_review_graph()      # compiled once at import, reused per job