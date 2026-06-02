"""
github_client.py – Thin wrapper around the GitHub REST API.
"""

import os
import base64
from typing import Optional
import requests
from loguru import logger

GITHUB_TOKEN = os.getenv("GH_TOKEN")
BASE = "https://api.github.com"


def _headers():
    if not GITHUB_TOKEN:
        raise EnvironmentError("GH_TOKEN environment variable is not set.")
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get(path: str, params: dict = None) -> dict:
    r = requests.get(f"{BASE}{path}", headers=_headers(), params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _post(path: str, body: dict) -> dict:
    r = requests.post(f"{BASE}{path}", headers=_headers(), json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def _patch(path: str, body: dict) -> dict:
    r = requests.patch(f"{BASE}{path}", headers=_headers(), json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def _put(path: str, body: dict) -> dict:
    r = requests.put(f"{BASE}{path}", headers=_headers(), json=body, timeout=30)
    r.raise_for_status()
    return r.json()


# ─── Repo ────────────────────────────────────────────────────────────────────

def get_repo(repo: str) -> dict:
    return _get(f"/repos/{repo}")


def get_repo_tree(repo: str, branch: str = "main") -> list:
    data = _get(f"/repos/{repo}/git/trees/{branch}?recursive=1")
    return data.get("tree", [])


def get_file_content(repo: str, path: str, branch: str = "main") -> str:
    """Return decoded file content as a string."""
    try:
        data = _get(f"/repos/{repo}/contents/{path}", params={"ref": branch})
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return ""
        raise


def get_file_sha(repo: str, path: str, branch: str = "main") -> Optional[str]:
    try:
        data = _get(f"/repos/{repo}/contents/{path}", params={"ref": branch})
        return data.get("sha")
    except requests.HTTPError:
        return None


def update_file(repo: str, path: str, content: str, message: str, branch: str = "main") -> dict:
    """Create or update a file in-place (no separate PR branch needed)."""
    sha = get_file_sha(repo, path, branch)
    body = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha
    return _put(f"/repos/{repo}/contents/{path}", body)


def get_default_branch(repo: str) -> str:
    data = get_repo(repo)
    return data.get("default_branch", "main")


# ─── Branches ────────────────────────────────────────────────────────────────

def create_branch(repo: str, branch_name: str, from_branch: str = "main") -> dict:
    ref_data = _get(f"/repos/{repo}/git/ref/heads/{from_branch}")
    sha = ref_data["object"]["sha"]
    try:
        return _post(f"/repos/{repo}/git/refs", {"ref": f"refs/heads/{branch_name}", "sha": sha})
    except requests.HTTPError as e:
        if e.response.status_code == 422:
            logger.info(f"Branch {branch_name} already exists.")
            return {}
        raise


def branch_exists(repo: str, branch_name: str) -> bool:
    try:
        _get(f"/repos/{repo}/git/ref/heads/{branch_name}")
        return True
    except requests.HTTPError:
        return False


# ─── Pull Requests ───────────────────────────────────────────────────────────

def create_pr(repo: str, title: str, body: str, head: str, base: str = "main") -> dict:
    return _post(f"/repos/{repo}/pulls", {"title": title, "body": body, "head": head, "base": base})


def list_open_prs(repo: str) -> list:
    return _get(f"/repos/{repo}/pulls", params={"state": "open", "per_page": 50})


def get_pr(repo: str, pr_number: int) -> dict:
    return _get(f"/repos/{repo}/pulls/{pr_number}")


def add_pr_label(repo: str, pr_number: int, labels: list) -> dict:
    return _post(f"/repos/{repo}/issues/{pr_number}/labels", {"labels": labels})


def post_pr_review_comment(repo: str, pr_number: int, body: str) -> dict:
    return _post(f"/repos/{repo}/issues/{pr_number}/comments", {"body": body})


# ─── Issues ──────────────────────────────────────────────────────────────────

def list_open_issues(repo: str) -> list:
    issues = _get(f"/repos/{repo}/issues", params={"state": "open", "per_page": 100})
    # Filter out PRs (GitHub returns PRs in the issues list)
    return [i for i in issues if "pull_request" not in i]


def get_issue(repo: str, issue_number: int) -> dict:
    return _get(f"/repos/{repo}/issues/{issue_number}")


def add_labels_to_issue(repo: str, issue_number: int, labels: list) -> dict:
    return _post(f"/repos/{repo}/issues/{issue_number}/labels", {"labels": labels})


def post_comment(repo: str, issue_number: int, body: str) -> dict:
    return _post(f"/repos/{repo}/issues/{issue_number}/comments", {"body": body})


def close_issue(repo: str, issue_number: int) -> dict:
    return _patch(f"/repos/{repo}/issues/{issue_number}", {"state": "closed"})


def search_issues(repo: str, query: str) -> list:
    q = f"{query} repo:{repo} is:issue is:open"
    data = _get("/search/issues", params={"q": q, "per_page": 10})
    return data.get("items", [])


def ensure_labels_exist(repo: str) -> None:
    """Create standard labels if they don't exist."""
    existing = {l["name"] for l in _get(f"/repos/{repo}/labels", params={"per_page": 100})}
    standard = [
        ("bug", "d73a4a", "Something isn't working"),
        ("enhancement", "a2eeef", "New feature or request"),
        ("question", "d876e3", "Further information is requested"),
        ("documentation", "0075ca", "Improvements or additions to documentation"),
        ("duplicate", "cfd3d7", "This issue or pull request already exists"),
        ("good first issue", "7057ff", "Good for newcomers"),
        ("security", "e4e669", "Security vulnerability"),
        ("dependencies", "0366d6", "Pull requests that update a dependency file"),
        ("bot", "b4a8d1", "Automated by Git-Repo-Caretaker"),
    ]
    for name, color, desc in standard:
        if name not in existing:
            try:
                _post(f"/repos/{repo}/labels", {"name": name, "color": color, "description": desc})
                logger.info(f"Created label: {name}")
            except Exception as e:
                logger.warning(f"Could not create label '{name}': {e}")


# ─── Releases ────────────────────────────────────────────────────────────────

def list_releases(repo: str) -> list:
    return _get(f"/repos/{repo}/releases", params={"per_page": 10})


def get_latest_release(repo: str) -> Optional[dict]:
    try:
        return _get(f"/repos/{repo}/releases/latest")
    except requests.HTTPError:
        return None


def get_commits_since(repo: str, since_sha: str) -> list:
    return _get(f"/repos/{repo}/commits", params={"per_page": 50})


# ─── Actions / Workflows ─────────────────────────────────────────────────────

def get_workflow_runs(repo: str) -> list:
    data = _get(f"/repos/{repo}/actions/runs", params={"per_page": 5})
    return data.get("workflow_runs", [])


def get_ci_badge_url(repo: str, workflow_file: str = "ci.yml") -> str:
    return f"https://github.com/{repo}/actions/workflows/{workflow_file}/badge.svg"


# ─── Contents helpers ────────────────────────────────────────────────────────

def list_directory(repo: str, path: str = "", branch: str = "main") -> list:
    try:
        return _get(f"/repos/{repo}/contents/{path}", params={"ref": branch})
    except requests.HTTPError:
        return []
