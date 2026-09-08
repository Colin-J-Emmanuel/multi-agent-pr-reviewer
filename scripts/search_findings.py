import asyncio
import sys

from app.db import close_pool, search_similar_findings


async def main(query: str) -> None:
    results = await search_similar_findings(query)
    print(f"Findings similar to: {query!r}\n")
    for r in results:
        print(f"  [{r['distance']:.3f}] {r['repo']}#{r['pr_number']} "
              f"({r['category']}/{r['severity']}) {r['file']}")
        print(f"          {r['message'][:90]}")
    await close_pool()


if __name__ == "__main__":
    asyncio.run(main(" ".join(sys.argv[1:])))
