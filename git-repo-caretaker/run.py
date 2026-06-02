#!/usr/bin/env python
"""
run.py – One-shot CLI runner for all caretaker tasks.

Usage examples:
  python run.py readme  username/my-repo
  python run.py triage  username/my-repo --all
  python run.py triage  username/my-repo --issue 42
  python run.py triage  username/my-repo --pr 17
  python run.py deps    username/my-repo
  python run.py deps    username/my-repo --dry-run
  python run.py digest  username/my-repo --assignee alice
  python run.py agenda  username/my-repo
  python run.py release username/my-repo
  python run.py backup  --dir ~/notebooks --backup-repo username/backups
"""

import argparse
import json
import sys
import os
from dotenv import load_dotenv

load_dotenv()   # Load .env file automatically

import readme_bot
import triage_bot
import dependency_bot
import personal_assistant


def cmd_readme(args):
    result = readme_bot.run(args.repo, args.branch, args.force)
    print(json.dumps(result, indent=2))


def cmd_triage(args):
    if args.all:
        results = triage_bot.triage_all_open(args.repo)
        print(json.dumps(results, indent=2))
    elif args.issue is not None:
        result = triage_bot.triage_issue(args.repo, args.issue)
        print(json.dumps(result, indent=2))
    elif args.pr is not None:
        result = triage_bot.triage_pr(args.repo, args.pr)
        print(json.dumps(result, indent=2))
    else:
        print("Provide --issue N, --pr N, or --all")
        sys.exit(1)


def cmd_deps(args):
    result = dependency_bot.run(args.repo, args.branch, args.dry_run)
    print(json.dumps(result, indent=2))


def cmd_digest(args):
    result = personal_assistant.daily_todo_digest(args.repo, args.assignee, args.email)
    print(result.get("digest", json.dumps(result, indent=2)))


def cmd_agenda(args):
    result = personal_assistant.meeting_agenda(args.repo)
    print(result.get("agenda", json.dumps(result, indent=2)))


def cmd_release(args):
    result = personal_assistant.release_summary(args.repo)
    print(result.get("summary", json.dumps(result, indent=2)))


def cmd_backup(args):
    result = personal_assistant.backup_notebooks(args.dir, args.backup_repo)
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="Git-Repo-Caretaker CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # ── readme ────────────────────────────────────────────────────────────
    p = sub.add_parser("readme", help="Auto-generate / refresh README")
    p.add_argument("repo", help="owner/repo")
    p.add_argument("--branch", default="main")
    p.add_argument("--force", action="store_true")

    # ── triage ────────────────────────────────────────────────────────────
    p = sub.add_parser("triage", help="Triage issues / PRs")
    p.add_argument("repo", help="owner/repo")
    p.add_argument("--issue", type=int, metavar="N")
    p.add_argument("--pr", type=int, metavar="N")
    p.add_argument("--all", action="store_true", help="Triage all open issues")

    # ── deps ──────────────────────────────────────────────────────────────
    p = sub.add_parser("deps", help="Check & update dependencies")
    p.add_argument("repo", help="owner/repo")
    p.add_argument("--branch", default="main")
    p.add_argument("--dry-run", action="store_true")

    # ── digest ────────────────────────────────────────────────────────────
    p = sub.add_parser("digest", help="Daily to-do digest")
    p.add_argument("repo", help="owner/repo")
    p.add_argument("--assignee", required=True)
    p.add_argument("--email", action="store_true")

    # ── agenda ────────────────────────────────────────────────────────────
    p = sub.add_parser("agenda", help="Generate meeting agenda")
    p.add_argument("repo", help="owner/repo")

    # ── release ───────────────────────────────────────────────────────────
    p = sub.add_parser("release", help="Generate release summary")
    p.add_argument("repo", help="owner/repo")

    # ── backup ────────────────────────────────────────────────────────────
    p = sub.add_parser("backup", help="Backup notebooks to GitHub")
    p.add_argument("--dir", required=True, help="Local directory")
    p.add_argument("--backup-repo", required=True, help="GitHub backup repo")

    args = parser.parse_args()
    dispatch = {
        "readme":  cmd_readme,
        "triage":  cmd_triage,
        "deps":    cmd_deps,
        "digest":  cmd_digest,
        "agenda":  cmd_agenda,
        "release": cmd_release,
        "backup":  cmd_backup,
    }
    dispatch[args.cmd](args)


if __name__ == "__main__":
    main()
