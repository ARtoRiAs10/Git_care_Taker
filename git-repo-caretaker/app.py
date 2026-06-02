"""
app.py – FastAPI webhook server for GitHub events.

Endpoints:
  POST /webhook          – GitHub webhook receiver (HMAC-verified)
  GET  /health           – Health check
  POST /run/readme       – Manually trigger README update
  POST /run/triage/{n}  – Manually triage issue/PR
  POST /run/deps         – Manually run dependency check
  GET  /run/digest       – Generate daily to-do digest
  GET  /run/agenda       – Generate meeting agenda
"""

import os
import hmac
import hashlib
import json
from contextlib import asynccontextmanager

import yaml
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from loguru import logger

import readme_bot
import triage_bot
import dependency_bot
import personal_assistant
from llm_client import call_llm

# ─── Config ───────────────────────────────────────────────────────────────────

CONFIG_PATH = os.getenv("CONFIG_PATH", "config.yaml")


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


CFG: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global CFG
    CFG = load_config()
    repo = CFG.get("repo", os.getenv("GITHUB_REPO", ""))
    maintainers = CFG.get("maintainers", [])
    triage_bot.set_maintainers(maintainers)
    logger.info(f"Git-Repo-Caretaker started. Repo: {repo or 'NOT SET'}")
    yield


app = FastAPI(title="Git-Repo-Caretaker", version="1.0.0", lifespan=lifespan)

# ─── Helpers ──────────────────────────────────────────────────────────────────

WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")


def _verify_signature(payload: bytes, sig_header: str) -> bool:
    if not WEBHOOK_SECRET:
        logger.warning("GITHUB_WEBHOOK_SECRET not set – skipping signature verification.")
        return True
    if not sig_header or not sig_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header)


def _repo() -> str:
    repo = CFG.get("repo", os.getenv("GITHUB_REPO", ""))
    if not repo:
        raise HTTPException(status_code=503, detail="GITHUB_REPO not configured.")
    return repo


def _branch() -> str:
    return CFG.get("branch", os.getenv("DEFAULT_BRANCH", "main"))


# ─── Health ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "repo": CFG.get("repo", "not-set")}


# ─── GitHub Webhook ───────────────────────────────────────────────────────────

@app.post("/webhook")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    payload_bytes = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_signature(payload_bytes, sig):
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")

    event = request.headers.get("X-GitHub-Event", "")
    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Bad JSON payload.")

    logger.info(f"Webhook received: {event}")
    repo = payload.get("repository", {}).get("full_name", _repo())

    # ── Issue opened ─────────────────────────────────────────────────────
    if event == "issues" and payload.get("action") == "opened":
        issue_number = payload["issue"]["number"]
        background_tasks.add_task(triage_bot.triage_issue, repo, issue_number)
        return {"queued": "issue_triage", "issue": issue_number}

    # ── PR opened ─────────────────────────────────────────────────────────
    if event == "pull_request" and payload.get("action") == "opened":
        pr_number = payload["pull_request"]["number"]
        background_tasks.add_task(triage_bot.triage_pr, repo, pr_number)
        return {"queued": "pr_triage", "pr": pr_number}

    # ── Push to main → maybe refresh README ──────────────────────────────
    if event == "push":
        ref = payload.get("ref", "")
        default_branch = _branch()
        if ref == f"refs/heads/{default_branch}":
            auto_readme = CFG.get("auto_readme_on_push", False)
            if auto_readme:
                background_tasks.add_task(readme_bot.run, repo, default_branch, False)
                return {"queued": "readme_update"}

    # ── Release published → generate post-mortem ─────────────────────────
    if event == "release" and payload.get("action") == "published":
        background_tasks.add_task(personal_assistant.release_summary, repo)
        return {"queued": "release_summary"}

    return {"status": "ignored", "event": event}


# ─── Manual trigger endpoints ─────────────────────────────────────────────────

@app.post("/run/readme")
async def run_readme(background_tasks: BackgroundTasks, force: bool = False):
    """Manually trigger README update."""
    repo = _repo()
    background_tasks.add_task(readme_bot.run, repo, _branch(), force)
    return {"queued": "readme_update", "repo": repo, "force": force}


@app.post("/run/triage/{number}")
async def run_triage(number: int, background_tasks: BackgroundTasks, kind: str = "issue"):
    """Manually triage a single issue or PR."""
    repo = _repo()
    if kind == "pr":
        background_tasks.add_task(triage_bot.triage_pr, repo, number)
    else:
        background_tasks.add_task(triage_bot.triage_issue, repo, number)
    return {"queued": f"{kind}_triage", "number": number}


@app.post("/run/triage-all")
async def run_triage_all(background_tasks: BackgroundTasks):
    """Triage all open issues."""
    repo = _repo()
    background_tasks.add_task(triage_bot.triage_all_open, repo)
    return {"queued": "triage_all", "repo": repo}


@app.post("/run/deps")
async def run_deps(background_tasks: BackgroundTasks, dry_run: bool = False):
    """Check and update dependencies."""
    repo = _repo()
    background_tasks.add_task(dependency_bot.run, repo, _branch(), dry_run)
    return {"queued": "dependency_check", "dry_run": dry_run}


@app.get("/run/digest")
async def run_digest(assignee: str = ""):
    """Generate daily to-do digest."""
    repo = _repo()
    user = assignee or CFG.get("maintainers", [""])[0]
    if not user:
        raise HTTPException(status_code=400, detail="Provide ?assignee=username")
    result = personal_assistant.daily_todo_digest(repo, user)
    return result


@app.get("/run/agenda")
async def run_agenda():
    """Generate meeting agenda."""
    repo = _repo()
    result = personal_assistant.meeting_agenda(repo)
    return result


@app.get("/run/release-summary")
async def run_release_summary():
    """Generate release summary."""
    repo = _repo()
    result = personal_assistant.release_summary(repo)
    return result


@app.post("/run/backup")
async def run_backup(local_dir: str, backup_repo: str):
    """Backup local notebooks to GitHub."""
    result = personal_assistant.backup_notebooks(local_dir, backup_repo)
    return result


# ─── LLM chat endpoint (debugging helper) ─────────────────────────────────────

@app.post("/chat")
async def chat(request: Request):
    """Debug endpoint – sends a raw prompt to the LLM."""
    body = await request.json()
    prompt = body.get("prompt", "")
    if not prompt:
        raise HTTPException(status_code=400, detail="Provide {\"prompt\": \"...\"}")
    response = call_llm(prompt)
    return {"response": response}


# ─── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
