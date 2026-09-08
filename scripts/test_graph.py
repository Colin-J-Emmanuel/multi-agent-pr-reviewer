# scripts/test_graph.py
import asyncio
import sys

from app.github_client import GitHubClient
from app.graph import review_graph


async def main(repo: str, pr_number: int) -> None:
    context = await GitHubClient().fetch_pr(repo, pr_number)
    result = await review_graph.ainvoke({
        "context": context, "findings": [],
        "high_findings": [], "low_findings": [], "summary": "",
    })

    print(f"\n=== HIGH-CONFIDENCE ({len(result['high_findings'])}) ===")
    for f in result["high_findings"]:
        print(f"  [{f.category}/{f.severity}] {f.file} ({f.confidence}) — {f.message[:70]}")

    print(f"\n=== LOW-CONFIDENCE / demoted ({len(result['low_findings'])}) ===")
    for f in result["low_findings"]:
        print(f"  [{f.category}/{f.severity}] {f.file} ({f.confidence}) — {f.message[:70]}")

    print(f"\n=== SUMMARY ===\n{result['summary']}\n")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], int(sys.argv[2])))
