import asyncio
import sys

from app import graph
from app.github_client import GitHubClient

AGENTS = {
    "security": graph.security_agent,
    "quality": graph.quality_agent,
    "testing": graph.testing_agent,
    "docs": graph.docs_agent,
}


async def main(agent_name: str, repo: str, pr_number: int) -> None:
    agent = AGENTS[agent_name]
    context = await GitHubClient().fetch_pr(repo, pr_number)
    result = await agent({"context": context, "findings": []})
    findings = result["findings"]
    print(f"{agent_name} agent returned {len(findings)} finding(s):")
    for f in findings:
        loc = f"{f.file}:{f.line}" if f.line else f.file
        print(f"  [{f.severity}] {loc} (confidence {f.confidence}) — {f.message}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], int(sys.argv[3])))
