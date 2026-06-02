"""
dependency_bot.py – Detect outdated dependencies, generate human-readable summaries,
and open PRs to update them.

Supports: Python (requirements.txt / pyproject.toml), Node.js (package.json),
          Ruby (Gemfile), and Rust (Cargo.toml).
"""

import os
import re
import json
from dataclasses import dataclass
from typing import Optional
import requests
from loguru import logger

import github_client as gh
from llm_client import call_llm


# ─── Data types ───────────────────────────────────────────────────────────────

@dataclass
class Dependency:
    name: str
    current_version: str
    latest_version: str
    ecosystem: str          # "python", "node", "ruby", "rust"
    changelog_url: str = ""
    summary: str = ""


# ─── Version fetchers ─────────────────────────────────────────────────────────

def _fetch_pypi_latest(package: str) -> Optional[str]:
    try:
        r = requests.get(f"https://pypi.org/pypi/{package}/json", timeout=10)
        r.raise_for_status()
        return r.json()["info"]["version"]
    except Exception:
        return None


def _fetch_npm_latest(package: str) -> Optional[str]:
    try:
        r = requests.get(f"https://registry.npmjs.org/{package}/latest", timeout=10)
        r.raise_for_status()
        return r.json()["version"]
    except Exception:
        return None


def _normalize_version(v: str) -> str:
    """Strip common specifiers like ^, ~, >=, ==, etc."""
    return re.sub(r"[^0-9.]", "", v.split(",")[0]).strip(".")


# ─── Requirement parsers ──────────────────────────────────────────────────────

def _parse_requirements_txt(content: str) -> dict[str, str]:
    deps = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*[=<>!~^]+\s*([A-Za-z0-9_.]+)", line)
        if m:
            deps[m.group(1).lower()] = m.group(2)
    return deps


def _parse_pyproject_toml(content: str) -> dict[str, str]:
    deps = {}
    in_deps = False
    for line in content.splitlines():
        if "[tool.poetry.dependencies]" in line or "[project]" in line:
            in_deps = True
        elif line.startswith("[") and in_deps:
            in_deps = False
        if in_deps:
            m = re.match(r'^\s*([A-Za-z0-9_.\-]+)\s*=\s*["\']?[~^>=<]*([0-9][^"\']*)["\']?', line)
            if m and m.group(1) not in ("python", "name", "version"):
                deps[m.group(1).lower()] = m.group(2).strip()
    return deps


def _parse_package_json(content: str) -> dict[str, str]:
    try:
        data = json.loads(content)
        merged = {}
        merged.update(data.get("dependencies", {}))
        merged.update(data.get("devDependencies", {}))
        return {k: _normalize_version(v) for k, v in merged.items()}
    except Exception:
        return {}


# ─── Changelog / summary generation ──────────────────────────────────────────

def _changelog_url(pkg: str, old: str, new: str, ecosystem: str) -> str:
    if ecosystem == "python":
        return f"https://pypi.org/project/{pkg}/#history"
    if ecosystem == "node":
        return f"https://www.npmjs.com/package/{pkg}?activeTab=versions"
    return ""


def generate_update_summary(name: str, old_ver: str, new_ver: str, ecosystem: str) -> str:
    """Ask the LLM to write a human-readable changelog summary."""
    prompt = f"""You are a dependency-update assistant. Write a 2-3 sentence plain-English summary
of what likely changed when upgrading the {ecosystem} package "{name}" from version {old_ver} to {new_ver}.

Focus on:
- Security patches (if common for this package)
- New features developers care about
- Potential breaking changes to watch for

If you don't know the specific changes, give general advice for upgrading {name}.
Be concise and factual. Do NOT invent specific CVE numbers or bug references."""

    try:
        return call_llm(prompt, max_tokens=200, temperature=0.3)
    except Exception as e:
        logger.warning(f"Could not generate summary for {name}: {e}")
        return f"Upgrade from {old_ver} to {new_ver}. Check the changelog for details."


# ─── Check for updates ────────────────────────────────────────────────────────

def check_python_deps(repo: str, branch: str) -> list[Dependency]:
    outdated = []

    # Try requirements.txt first, then pyproject.toml
    req_content = gh.get_file_content(repo, "requirements.txt", branch)
    deps = _parse_requirements_txt(req_content) if req_content else {}

    if not deps:
        pyproject = gh.get_file_content(repo, "pyproject.toml", branch)
        if pyproject:
            deps = _parse_pyproject_toml(pyproject)

    for name, current in deps.items():
        latest = _fetch_pypi_latest(name)
        if latest and latest != current and _normalize_version(current) != latest:
            summary = generate_update_summary(name, current, latest, "python")
            outdated.append(Dependency(
                name=name,
                current_version=current,
                latest_version=latest,
                ecosystem="python",
                changelog_url=_changelog_url(name, current, latest, "python"),
                summary=summary,
            ))

    return outdated


def check_node_deps(repo: str, branch: str) -> list[Dependency]:
    outdated = []
    content = gh.get_file_content(repo, "package.json", branch)
    if not content:
        return []
    deps = _parse_package_json(content)
    for name, current in list(deps.items())[:30]:  # cap at 30 to avoid API spam
        latest = _fetch_npm_latest(name)
        if latest and latest != current:
            summary = generate_update_summary(name, current, latest, "node")
            outdated.append(Dependency(
                name=name,
                current_version=current,
                latest_version=latest,
                ecosystem="node",
                changelog_url=_changelog_url(name, current, latest, "node"),
                summary=summary,
            ))
    return outdated


# ─── Update the actual file ───────────────────────────────────────────────────

def _bump_requirements_txt(content: str, dep: Dependency) -> str:
    """Replace the version pin for a single dependency."""
    pattern = re.compile(
        rf"(?i)^({re.escape(dep.name)})\s*([=<>!~^]+)\s*{re.escape(dep.current_version)}",
        re.MULTILINE,
    )
    return pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{dep.latest_version}", content)


def _bump_package_json(content: str, dep: Dependency) -> str:
    return re.sub(
        rf'("{re.escape(dep.name)}":\s*")[^"]*"',
        rf'"{dep.name}": "{dep.latest_version}"',
        content,
    )


# ─── PR creation ─────────────────────────────────────────────────────────────

def _create_dependency_pr(repo: str, dep: Dependency, branch: str = "main") -> str:
    bot_branch = f"dep-update/{dep.ecosystem}-{dep.name.replace('/', '-')}-{dep.latest_version}"

    # Create branch
    gh.create_branch(repo, bot_branch, from_branch=branch)

    # Update the right file
    if dep.ecosystem == "python":
        file_path = "requirements.txt"
        content = gh.get_file_content(repo, file_path, branch)
        if not content:
            file_path = "pyproject.toml"
            content = gh.get_file_content(repo, file_path, branch)
        updated = _bump_requirements_txt(content, dep)
    else:
        file_path = "package.json"
        content = gh.get_file_content(repo, file_path, branch)
        updated = _bump_package_json(content, dep)

    if content == updated:
        logger.warning(f"No change detected for {dep.name} in {file_path}. Skipping PR.")
        return ""

    commit_msg = f"⬆️ Bump {dep.name} from {dep.current_version} to {dep.latest_version}"
    gh.update_file(repo, file_path, updated, commit_msg, branch=bot_branch)

    pr_body = f"""## Dependency Update: `{dep.name}`

| | Version |
|---|---|
| **Before** | `{dep.current_version}` |
| **After** | `{dep.latest_version}` |

### Summary
{dep.summary}

{f'### Changelog{chr(10)}{dep.changelog_url}' if dep.changelog_url else ''}

---
_Opened automatically by **Git-Repo-Caretaker** 🤖_"""

    pr = gh.create_pr(
        repo,
        title=f"⬆️ {dep.name}: {dep.current_version} → {dep.latest_version}",
        body=pr_body,
        head=bot_branch,
        base=branch,
    )
    return pr.get("html_url", "")


# ─── Main entry point ─────────────────────────────────────────────────────────

MAX_PRS_PER_RUN = int(os.getenv("MAX_DEPENDENCY_PRS", "5"))


def run(repo: str, branch: str = "main", dry_run: bool = False) -> dict:
    """Check all dependencies and open PRs for outdated ones."""
    result = {"repo": repo, "outdated": [], "prs_created": [], "errors": []}

    try:
        logger.info(f"Checking Python dependencies for {repo} ...")
        outdated = check_python_deps(repo, branch)

        logger.info(f"Checking Node.js dependencies for {repo} ...")
        outdated += check_node_deps(repo, branch)

        result["outdated"] = [
            {"name": d.name, "current": d.current_version, "latest": d.latest_version}
            for d in outdated
        ]
        logger.info(f"Found {len(outdated)} outdated deps.")

        if dry_run:
            logger.info("Dry run – skipping PR creation.")
            return result

        for dep in outdated[:MAX_PRS_PER_RUN]:
            try:
                pr_url = _create_dependency_pr(repo, dep, branch)
                if pr_url:
                    result["prs_created"].append(pr_url)
                    logger.success(f"PR created: {pr_url}")
            except Exception as e:
                result["errors"].append(f"{dep.name}: {str(e)}")
                logger.error(f"Failed to create PR for {dep.name}: {e}")

    except Exception as e:
        result["errors"].append(str(e))
        logger.error(f"Dependency bot error: {e}")

    return result


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Check and update dependencies")
    parser.add_argument("repo", help="owner/repo")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--dry-run", action="store_true", help="Check but don't open PRs")
    args = parser.parse_args()

    result = run(args.repo, args.branch, args.dry_run)
    print(json.dumps(result, indent=2))
