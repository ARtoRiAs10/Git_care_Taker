"""
personal_assistant.py – Optional personal-assistant extensions.

Features:
  1. release_summary   – LLM-written post-mortem / changelog for the latest release.
  2. daily_todo_digest – Summarise open issues assigned to you; optionally send by email.
  3. backup_notebooks  – Zip local .ipynb / .py files and push to a private GitHub repo.
  4. meeting_agenda    – Generate a bullet-point agenda from recent PRs & issues.
"""

import os
import io
import json
import zipfile
import base64
import smtplib
from datetime import datetime, timezone
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from loguru import logger

import github_client as gh
from llm_client import call_llm


# ─── 1. Release summary ───────────────────────────────────────────────────────

def release_summary(repo: str) -> dict:
    """Generate an LLM-written post-mortem summary for the latest release."""
    result = {"action": "none", "release": None, "summary": ""}

    try:
        release = gh.get_latest_release(repo)
        if not release:
            result["action"] = "no_release"
            return result

        tag = release.get("tag_name", "")
        name = release.get("name", tag)
        body = release.get("body", "") or ""
        published = release.get("published_at", "")

        # Fetch recent commits for context
        commits = gh.get_commits_since(repo, "")
        commit_msgs = [c["commit"]["message"].splitlines()[0] for c in commits[:20]]

        prompt = f"""You are a technical release manager writing a professional post-mortem / release summary.

Repository: {repo}
Release: {name} ({tag})
Published: {published}
Original release notes:
{body[:1000] or '(none provided)'}

Recent commit messages:
{json.dumps(commit_msgs, indent=2)}

Write a concise post-mortem Markdown document with these sections:
1. **Release Overview** – one paragraph summary
2. **Key Changes** – bullet list of the 5 most important changes
3. **Metrics / Impact** – placeholders for: lines changed, files touched, contributors
4. **Known Issues / Follow-up** – any issues opened since the release (leave placeholder if unknown)
5. **Acknowledgements** – thank contributors

Keep it professional and under 300 words."""

        summary = call_llm(prompt, max_tokens=600, temperature=0.3)
        result.update({"action": "generated", "release": name, "summary": summary})
        logger.success(f"Release summary generated for {name}")

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Release summary error: {e}")

    return result


# ─── 2. Daily to-do digest ────────────────────────────────────────────────────

def daily_todo_digest(repo: str, assignee: str, send_email: bool = False) -> dict:
    """Summarise all open issues assigned to `assignee` and optionally email them."""
    result = {"action": "none", "issue_count": 0, "digest": ""}

    try:
        all_issues = gh.list_open_issues(repo)
        my_issues = [
            i for i in all_issues
            if any(a["login"] == assignee for a in i.get("assignees", []))
        ]
        result["issue_count"] = len(my_issues)

        if not my_issues:
            result["action"] = "no_issues"
            result["digest"] = f"✅ No open issues assigned to @{assignee}. Great work!"
            return result

        issue_list = "\n".join(
            f"- #{i['number']}: {i['title']} (labels: {', '.join(l['name'] for l in i.get('labels', []))})"
            for i in my_issues[:20]
        )

        today = datetime.now(timezone.utc).strftime("%A, %B %-d %Y")
        prompt = f"""You are a productivity assistant writing a daily to-do digest.

Date: {today}
GitHub user: @{assignee}
Repository: {repo}

Open issues assigned to them:
{issue_list}

Write a friendly, motivating daily digest in plain text (no Markdown):
1. Greeting with the date.
2. Summary sentence ("You have X open issues across N categories.")
3. Top 3 priority issues with a one-line action item for each.
4. A short motivating closing line.

Keep it under 200 words."""

        digest = call_llm(prompt, max_tokens=350, temperature=0.5)
        result.update({"action": "generated", "digest": digest})

        if send_email:
            _send_digest_email(assignee, digest, today)
            result["email_sent"] = True

        logger.success(f"Daily digest generated for @{assignee}")

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Daily digest error: {e}")

    return result


def _send_digest_email(to_user: str, digest: str, date: str):
    """Send digest via SMTP. Reads config from env vars."""
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    to_email  = os.getenv("DIGEST_EMAIL")

    if not all([smtp_user, smtp_pass, to_email]):
        logger.warning("SMTP not configured. Skipping email.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📋 Your GitHub To-Do Digest – {date}"
    msg["From"] = smtp_user
    msg["To"] = to_email
    msg.attach(MIMEText(digest, "plain"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, to_email, msg.as_string())

    logger.success(f"Digest email sent to {to_email}")


# ─── 3. Notebook / file backup ────────────────────────────────────────────────

def backup_notebooks(
    local_dir: str,
    backup_repo: str,
    file_extensions: list = None,
) -> dict:
    """
    Zip all notebooks / scripts in `local_dir` and push the archive to `backup_repo`
    under backups/YYYY-MM-DD.zip.
    """
    extensions = file_extensions or [".ipynb", ".py", ".R", ".sql"]
    result = {"action": "none", "files_backed_up": 0, "path": ""}

    try:
        source = Path(local_dir).expanduser()
        if not source.exists():
            result["error"] = f"Directory not found: {local_dir}"
            return result

        files = [f for f in source.rglob("*") if f.suffix in extensions and f.is_file()]
        if not files:
            result["action"] = "nothing_to_backup"
            return result

        # Build in-memory zip
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                zf.write(f, f.relative_to(source))
        buf.seek(0)
        zip_bytes = buf.read()

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        archive_path = f"backups/{date_str}.zip"

        # Push to GitHub
        existing_sha = gh.get_file_sha(backup_repo, archive_path)
        body = {
            "message": f"💾 Automated backup {date_str}",
            "content": base64.b64encode(zip_bytes).decode(),
        }
        if existing_sha:
            body["sha"] = existing_sha

        gh._put(f"/repos/{backup_repo}/contents/{archive_path}", body)

        result.update({
            "action": "backed_up",
            "files_backed_up": len(files),
            "path": archive_path,
        })
        logger.success(f"Backed up {len(files)} files to {backup_repo}/{archive_path}")

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Backup error: {e}")

    return result


# ─── 4. Meeting agenda ────────────────────────────────────────────────────────

def meeting_agenda(repo: str, n_prs: int = 5, n_issues: int = 5) -> dict:
    """Generate a bullet-point meeting agenda from recent PRs and open issues."""
    result = {"action": "none", "agenda": ""}

    try:
        open_prs = gh.list_open_prs(repo)[:n_prs]
        open_issues = gh.list_open_issues(repo)[:n_issues]

        pr_lines = [f"- PR #{p['number']}: {p['title']} (by @{p['user']['login']})" for p in open_prs]
        issue_lines = [
            f"- Issue #{i['number']}: {i['title']} (labels: {', '.join(l['name'] for l in i.get('labels', []))})"
            for i in open_issues
        ]

        today = datetime.now(timezone.utc).strftime("%A, %B %-d %Y")
        prompt = f"""You are a project manager generating a team sync meeting agenda.

Date: {today}
Repository: {repo}

Open Pull Requests ({len(open_prs)}):
{chr(10).join(pr_lines) or '(none)'}

Open Issues ({len(open_issues)}):
{chr(10).join(issue_lines) or '(none)'}

Write a structured meeting agenda in Markdown:
1. **Welcome & Quick Wins** (2 min)
2. **PR Review** – list each open PR as a bullet with a one-line question to resolve
3. **Issue Prioritization** – top 3 issues to address this sprint with brief rationale
4. **Blockers & Risks** – placeholder bullets
5. **Action Items** – auto-generated from the PRs/issues above
6. **Next Meeting** – placeholder

Keep it concise (under 250 words) and actionable."""

        agenda = call_llm(prompt, max_tokens=500, temperature=0.3)
        result.update({"action": "generated", "agenda": agenda})
        logger.success("Meeting agenda generated.")

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Meeting agenda error: {e}")

    return result


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Personal assistant extensions")
    parser.add_argument("repo", help="owner/repo")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("release-summary", help="Generate release post-mortem")

    p_todo = sub.add_parser("todo-digest", help="Daily issue digest")
    p_todo.add_argument("--assignee", required=True, help="GitHub username")
    p_todo.add_argument("--email", action="store_true", help="Send via email")

    p_backup = sub.add_parser("backup", help="Backup notebooks")
    p_backup.add_argument("--dir", required=True, help="Local directory to back up")
    p_backup.add_argument("--backup-repo", required=True, help="GitHub repo for backups")

    sub.add_parser("meeting-agenda", help="Generate meeting agenda")

    args = parser.parse_args()

    if args.cmd == "release-summary":
        print(json.dumps(release_summary(args.repo), indent=2))
    elif args.cmd == "todo-digest":
        print(json.dumps(daily_todo_digest(args.repo, args.assignee, args.email), indent=2))
    elif args.cmd == "backup":
        print(json.dumps(backup_notebooks(args.dir, args.backup_repo), indent=2))
    elif args.cmd == "meeting-agenda":
        print(json.dumps(meeting_agenda(args.repo), indent=2))
    else:
        parser.print_help()
