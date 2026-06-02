"""
scheduler.py – APScheduler-based cron runner.

Runs all periodic tasks based on the schedule defined in config.yaml.
Can be started standalone: `python scheduler.py`
"""

import os
import yaml
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

import readme_bot
import triage_bot
import dependency_bot
import personal_assistant

CONFIG_PATH = os.getenv("CONFIG_PATH", "config.yaml")


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


# ─── Task wrappers ────────────────────────────────────────────────────────────

def _run_readme():
    cfg = load_config()
    repo = cfg.get("repo", os.getenv("GITHUB_REPO", ""))
    if not repo:
        logger.error("GITHUB_REPO not configured.")
        return
    branch = cfg.get("branch", "main")
    logger.info(f"[SCHEDULER] Running README update for {repo}")
    result = readme_bot.run(repo, branch)
    logger.info(f"[SCHEDULER] README result: {result}")


def _run_triage_all():
    cfg = load_config()
    repo = cfg.get("repo", os.getenv("GITHUB_REPO", ""))
    if not repo:
        return
    triage_bot.set_maintainers(cfg.get("maintainers", []))
    logger.info(f"[SCHEDULER] Running issue triage for {repo}")
    results = triage_bot.triage_all_open(repo)
    logger.info(f"[SCHEDULER] Triaged {len(results)} issues.")


def _run_dep_check():
    cfg = load_config()
    repo = cfg.get("repo", os.getenv("GITHUB_REPO", ""))
    if not repo:
        return
    branch = cfg.get("branch", "main")
    logger.info(f"[SCHEDULER] Running dependency check for {repo}")
    result = dependency_bot.run(repo, branch)
    logger.info(f"[SCHEDULER] Dep check result: {result}")


def _run_daily_digest():
    cfg = load_config()
    repo = cfg.get("repo", os.getenv("GITHUB_REPO", ""))
    maintainers = cfg.get("maintainers", [])
    if not repo or not maintainers:
        return
    for user in maintainers:
        logger.info(f"[SCHEDULER] Generating digest for @{user}")
        result = personal_assistant.daily_todo_digest(
            repo, user, send_email=cfg.get("digest_email", False)
        )
        logger.info(f"[SCHEDULER] Digest for @{user}: {result.get('issue_count', 0)} issues")


def _run_notebook_backup():
    cfg = load_config()
    backup_dir = cfg.get("backup", {}).get("local_dir", "")
    backup_repo = cfg.get("backup", {}).get("github_repo", "")
    if backup_dir and backup_repo:
        logger.info(f"[SCHEDULER] Running notebook backup: {backup_dir} → {backup_repo}")
        result = personal_assistant.backup_notebooks(backup_dir, backup_repo)
        logger.info(f"[SCHEDULER] Backup result: {result}")


# ─── Scheduler setup ─────────────────────────────────────────────────────────

def build_scheduler() -> BlockingScheduler:
    cfg = load_config()
    schedule = cfg.get("schedule", {})

    sched = BlockingScheduler(timezone="UTC")

    # README – default: daily at 06:00 UTC
    readme_cron = schedule.get("readme_cron", "0 6 * * *")
    sched.add_job(_run_readme, CronTrigger.from_crontab(readme_cron), id="readme_update")
    logger.info(f"README update scheduled: {readme_cron}")

    # Triage – default: every hour
    triage_cron = schedule.get("triage_cron", "0 * * * *")
    sched.add_job(_run_triage_all, CronTrigger.from_crontab(triage_cron), id="triage_all")
    logger.info(f"Issue triage scheduled: {triage_cron}")

    # Dependency check – default: every Monday at 09:00 UTC
    deps_cron = schedule.get("deps_cron", "0 9 * * 1")
    sched.add_job(_run_dep_check, CronTrigger.from_crontab(deps_cron), id="dep_check")
    logger.info(f"Dependency check scheduled: {deps_cron}")

    # Daily digest – default: every day at 08:00 UTC
    digest_cron = schedule.get("digest_cron", "0 8 * * *")
    sched.add_job(_run_daily_digest, CronTrigger.from_crontab(digest_cron), id="daily_digest")
    logger.info(f"Daily digest scheduled: {digest_cron}")

    # Notebook backup – default: every day at 23:00 UTC
    if cfg.get("backup", {}).get("enabled", False):
        backup_cron = schedule.get("backup_cron", "0 23 * * *")
        sched.add_job(_run_notebook_backup, CronTrigger.from_crontab(backup_cron), id="backup")
        logger.info(f"Notebook backup scheduled: {backup_cron}")

    return sched


if __name__ == "__main__":
    logger.info("Starting Git-Repo-Caretaker scheduler ...")
    scheduler = build_scheduler()
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")
