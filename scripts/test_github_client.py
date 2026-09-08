import asyncio
import sys

from app.github_client import GitHubClient


async def main(repo: str, pr_number: int) -> None:
    context = await GitHubClient().fetch_pr(repo, pr_number)
    print(f"PR #{context['pr_number']}: {context['title']}")
    print(f"  {context['author']}: {context['head_ref']} → {context['base_ref']}")
    print(f"  {context['files_returned']} files returned, "
          f"{context['files_skipped']} skipped, "
          f"{context['total_changed_files']} total changed")
    for f in context["files"]:
        tag = " (truncated)" if f["patch_truncated"] else ""
        print(f"    {f['status']:8} +{f['additions']} -{f['deletions']}  {f['filename']}{tag}")


if __name__ == "__main__":
    repo = sys.argv[1]
    pr_number = int(sys.argv[2])
    asyncio.run(main(repo, pr_number))
