"""
triage_bot.py – LLM-powered issue & PR triage assistant.

Capabilities:
  • Classify issues with labels (bug, enhancement, question, …)
  • Detect potential duplicate issues via GitHub Search API
  • Post a friendly first-response comment
  • Auto-assign to the right maintainer based on component
  • Add summary comment to new PRs with review checklist
"""

import os
import json
from typing import Optional
from loguru import logger

import github_client as gh
from llm_client import call_llm

# Load maintainer config from env or config.yaml (populated by app.py / scheduler)
MAINTAINERS: list = []  # populated via set_maintainers()


def set_maintainers(maintainers: list):
    global MAINTAINERS
    MAINTAINERS = maintainers


# ─── Label classification ─────────────────────────────────────────────────────

VALID_LABELS = [
    "bug", "enhancement", "question", "documentation",
    "duplicate", "good first issue", "security",
    "dependencies", "performance", "wontfix",
]


def classify_issue(title: str, body: str) -> list[str]:
    """Ask the LLM to return a JSON list of appropriate labels."""
    prompt = f"""Classify the following GitHub issue and return a JSON array of labels.

Issue title: {title}
Issue body (first 400 chars): {body[:400]}

Available labels: {json.dumps(VALID_LABELS)}

Rules:
- Return between 1 and 3 labels.
- Pick labels that are clearly justified by the title/body.
- If the body mentions a crash, stack trace, or "error", include "bug".
- If it mentions "feature", "add", "support", "would like", include "enhancement".
- If it ends with "?", include "question".
- If it mentions passwords, CVE, injection, XSS, include "security".
- ONLY return the JSON array, nothing else.

Example output: ["bug", "good first issue"]"""

    try:
        result = call_llm(prompt, json_mode=True, max_tokens=100)
        if isinstance(result, list):
            valid = [l for l in result if l in VALID_LABELS]
            return valid or ["question"]
        return ["question"]
    except Exception as e:
        logger.warning(f"Label classification failed: {e}")
        return ["question"]


# ─── Duplicate detection ─────────────────────────────────────────────────────

def find_duplicates(repo: str, title: str, current_number: int) -> list:
    """Search for open issues with similar titles."""
    # Use first ~5 significant words as search terms
    keywords = " ".join(title.split()[:5])
    similar = gh.search_issues(repo, keywords)
    return [i for i in similar if i["number"] != current_number]


# ─── Assignee selection ───────────────────────────────────────────────────────

def suggest_assignee(title: str, body: str, maintainers: list) -> Optional[str]:
    """Use the LLM to pick the most appropriate maintainer."""
    if not maintainers:
        return None
    prompt = f"""Given this GitHub issue, pick the SINGLE best maintainer username from the list.

Issue: {title}
Body: {body[:300]}

Maintainers: {json.dumps(maintainers)}

Return ONLY the username string, nothing else. If you cannot decide, return "{maintainers[0]}"."""
    try:
        choice = call_llm(prompt, max_tokens=30, temperature=0.1).strip().strip('"')
        return choice if choice in maintainers else maintainers[0]
    except Exception:
        return maintainers[0] if maintainers else None


# ─── Comment drafting ─────────────────────────────────────────────────────────

def draft_issue_comment(title: str, body: str, labels: list, duplicates: list) -> str:
    dup_note = ""
    if duplicates:
        dup_links = ", ".join(f"#{d['number']}" for d in duplicates[:3])
        dup_note = f"\n\n> ⚠️ Possible duplicates found: {dup_links}. Please check if your issue is already covered."

    prompt = f"""Write a short, friendly GitHub issue first-response comment.

Issue title: {title}
Issue body: {body[:300]}
Labels assigned: {labels}
{f'Possible duplicates: {[d["title"] for d in duplicates[:2]]}' if duplicates else ''}

The comment should:
1. Thank the reporter.
2. Acknowledge the issue type (bug / feature / question based on labels).
3. If it looks like a bug – ask for: OS, version, minimal reproduction steps.
4. If it's an enhancement – ask for the use-case / motivation.
5. If it's a question – offer to help clarify.
6. Be concise (3-5 sentences max).
7. End with: "_— Git-Repo-Caretaker Bot 🤖_"

Return ONLY the comment text."""

    comment = call_llm(prompt, max_tokens=300, temperature=0.4)
    return comment + dup_note


def draft_pr_comment(pr_title: str, pr_body: str, changed_files: list) -> str:
    files_str = "\n".join(f"- `{f}`" for f in changed_files[:15])
    prompt = f"""Write a concise PR review checklist comment for a GitHub pull request.

PR title: {pr_title}
PR description: {pr_body[:400]}
Changed files:
{files_str}

The comment should include:
1. A one-sentence summary of what this PR does.
2. A short review checklist (5-7 items), e.g.:
   - [ ] Tests added/updated
   - [ ] Documentation updated
   - [ ] No breaking changes (or migration guide provided)
   - [ ] Code follows project style
   - [ ] CI passes
3. End with: "_— Git-Repo-Caretaker Bot 🤖_"

Return ONLY the Markdown comment."""

    return call_llm(prompt, max_tokens=400, temperature=0.3)


# ─── Issue triage ─────────────────────────────────────────────────────────────

def triage_issue(repo: str, issue_number: int, skip_comment: bool = False) -> dict:
    """Full triage pipeline for a single issue."""
    result = {"issue": issue_number, "labels": [], "assignee": None, "comment_posted": False, "duplicates": []}

    try:
        issue = gh.get_issue(repo, issue_number)
        title = issue.get("title", "")
        body = issue.get("body", "") or ""

        # 1. Ensure standard labels exist in the repo
        gh.ensure_labels_exist(repo)

        # 2. Classify
        labels = classify_issue(title, body)
        result["labels"] = labels
        gh.add_labels_to_issue(repo, issue_number, labels)
        logger.info(f"Issue #{issue_number}: labels={labels}")

        # 3. Duplicate detection
        duplicates = find_duplicates(repo, title, issue_number)
        result["duplicates"] = [d["number"] for d in duplicates]
        if duplicates:
            logger.info(f"Issue #{issue_number}: potential duplicates {result['duplicates']}")
            if "duplicate" not in labels:
                gh.add_labels_to_issue(repo, issue_number, ["duplicate"])

        # 4. Assignee
        if MAINTAINERS:
            assignee = suggest_assignee(title, body, MAINTAINERS)
            if assignee:
                result["assignee"] = assignee
                try:
                    gh._patch(f"/repos/{repo}/issues/{issue_number}", {"assignees": [assignee]})
                except Exception as e:
                    logger.warning(f"Could not assign issue: {e}")

        # 5. Post comment
        if not skip_comment:
            comment = draft_issue_comment(title, body, labels, duplicates)
            gh.post_comment(repo, issue_number, comment)
            result["comment_posted"] = True

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Issue triage error for #{issue_number}: {e}")

    return result


# ─── PR triage ────────────────────────────────────────────────────────────────

def triage_pr(repo: str, pr_number: int) -> dict:
    """Triage a pull request: label it and post a review checklist."""
    result = {"pr": pr_number, "labels": [], "comment_posted": False}
    try:
        pr = gh.get_pr(repo, pr_number)
        title = pr.get("title", "")
        body = pr.get("body", "") or ""

        gh.ensure_labels_exist(repo)

        # PR-specific labels
        pr_labels = []
        if any(k in title.lower() for k in ["fix", "bug", "patch"]):
            pr_labels.append("bug")
        if any(k in title.lower() for k in ["feat", "add", "new", "implement"]):
            pr_labels.append("enhancement")
        if any(k in title.lower() for k in ["doc", "readme", "changelog"]):
            pr_labels.append("documentation")
        if not pr_labels:
            pr_labels.append("enhancement")

        gh.add_pr_label(repo, pr_number, pr_labels)
        result["labels"] = pr_labels

        # Get changed files
        files_data = gh._get(f"/repos/{repo}/pulls/{pr_number}/files")
        changed_files = [f["filename"] for f in files_data]

        comment = draft_pr_comment(title, body, changed_files)
        gh.post_pr_review_comment(repo, pr_number, comment)
        result["comment_posted"] = True
        logger.success(f"PR #{pr_number} triaged.")

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"PR triage error for #{pr_number}: {e}")

    return result


# ─── Batch triage all open issues ─────────────────────────────────────────────

def triage_all_open(repo: str) -> list:
    """Triage every open issue that hasn't been touched by the bot yet."""
    results = []
    issues = gh.list_open_issues(repo)
    logger.info(f"Found {len(issues)} open issues.")
    for issue in issues:
        number = issue["number"]
        # Skip if already has a bot label/comment
        labels = [l["name"] for l in issue.get("labels", [])]
        if "bot" in labels:
            logger.debug(f"Issue #{number} already processed, skipping.")
            continue
        result = triage_issue(repo, number)
        results.append(result)
    return results


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Triage GitHub issues/PRs")
    parser.add_argument("repo", help="owner/repo")
    sub = parser.add_subparsers(dest="cmd")

    p_issue = sub.add_parser("issue", help="Triage a single issue")
    p_issue.add_argument("number", type=int)

    p_pr = sub.add_parser("pr", help="Triage a single PR")
    p_pr.add_argument("number", type=int)

    p_all = sub.add_parser("all", help="Triage all open issues")

    args = parser.parse_args()

    if args.cmd == "issue":
        print(json.dumps(triage_issue(args.repo, args.number), indent=2))
    elif args.cmd == "pr":
        print(json.dumps(triage_pr(args.repo, args.number), indent=2))
    elif args.cmd == "all":
        print(json.dumps(triage_all_open(args.repo), indent=2))
    else:
        parser.print_help()
