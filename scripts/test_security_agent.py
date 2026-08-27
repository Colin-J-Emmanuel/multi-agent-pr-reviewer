import asyncio
import sys

from app.github_client import GitHubClient
from app.graph import security_agent


async def main(repo: str, pr_number: int) -> None:
    context = await GitHubClient().fetch_pr(repo, pr_number)
    result = await security_agent({"context": context, "findings": []})
    findings = result["findings"]
    print(f"Security agent returned {len(findings)} finding(s):")
    for f in findings:
        loc = f"{f.file}:{f.line}" if f.line else f.file
        print(f"  [{f.severity}] {loc} (confidence {f.confidence}) — {f.message}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], int(sys.argv[2])))