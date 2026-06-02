"""
readme_bot.py – Auto-generate / refresh README.md using an LLM.

Flow:
  1. Fetch the repo file tree from GitHub API.
  2. Sample key source files, docstrings, and config files.
  3. Detect CI badges.
  4. Prompt the LLM to write / update the README.
  5. If the result is meaningfully different, create a bot branch and open a PR.
"""

import os
import hashlib
import re
from pathlib import Path
from loguru import logger

import github_client as gh
from llm_client import call_llm


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _levenshtein_ratio(a: str, b: str) -> float:
    """Very cheap approximate similarity (based on length difference + shared n-grams)."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    set_a = set(a.split())
    set_b = set(b.split())
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union else 0.0


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ─── Metadata extraction ─────────────────────────────────────────────────────

INTERESTING_PATHS = {
    "configs": [
        "pyproject.toml", "setup.py", "setup.cfg",
        "package.json", "Cargo.toml", "go.mod",
        "composer.json", "pom.xml",
    ],
    "requirements": [
        "requirements.txt", "requirements-dev.txt",
        "Pipfile", "poetry.lock",
    ],
    "ci": [
        ".github/workflows/ci.yml",
        ".github/workflows/test.yml",
        ".github/workflows/build.yml",
    ],
    "docs": [
        "docs/index.md", "docs/README.md",
        "CONTRIBUTING.md", "CHANGELOG.md", "LICENSE",
    ],
}

MAX_FILE_CHARS = 6000  # cap each sampled file to avoid huge prompts


def extract_repo_metadata(repo: str, branch: str = "main") -> dict:
    """Return a dict with repo info ready to pass to the LLM."""
    logger.info(f"Fetching repo tree for {repo} ...")
    tree = gh.get_repo_tree(repo, branch)
    tree_paths = {entry["path"] for entry in tree}

    meta = {
        "repo": repo,
        "branch": branch,
        "modules": [],
        "source_samples": {},
        "configs": {},
        "badges": [],
        "existing_readme": "",
    }

    # ── Python / JS modules ───────────────────────────────────────────────
    for entry in tree:
        p = entry["path"]
        if p.endswith("__init__.py") and entry.get("size", 0) < 5000:
            meta["modules"].append(p)
        if p.endswith((".py", ".ts", ".js")) and "test" not in p.lower():
            if entry.get("size", 0) < 8000 and len(meta["source_samples"]) < 5:
                content = gh.get_file_content(repo, p, branch)
                meta["source_samples"][p] = content[:MAX_FILE_CHARS]

    # ── Config / manifest files ───────────────────────────────────────────
    for category, paths in INTERESTING_PATHS.items():
        for p in paths:
            if p in tree_paths:
                content = gh.get_file_content(repo, p, branch)
                meta["configs"][p] = content[:MAX_FILE_CHARS]
                break  # one per category is enough

    # ── CI badges ─────────────────────────────────────────────────────────
    for workflow_path in [".github/workflows/ci.yml", ".github/workflows/test.yml"]:
        if workflow_path in tree_paths:
            filename = Path(workflow_path).name
            meta["badges"].append(
                f"[![CI](https://github.com/{repo}/actions/workflows/{filename}/badge.svg)]"
                f"(https://github.com/{repo}/actions/workflows/{filename})"
            )

    # ── Existing README ───────────────────────────────────────────────────
    for readme_name in ["README.md", "readme.md", "README.rst"]:
        if readme_name in tree_paths:
            meta["existing_readme"] = gh.get_file_content(repo, readme_name, branch)[:6000]
            break

    return meta


# ─── README generation ───────────────────────────────────────────────────────

def generate_readme(meta: dict) -> str:
    repo_name = meta["repo"].split("/")[-1]
    badges_str = "\n".join(meta["badges"]) if meta["badges"] else "_(no CI badges detected)_"
    configs_str = "\n\n".join(
        f"**{k}**:\n```\n{v[:800]}\n```" for k, v in meta["configs"].items()
    ) or "_(no config files found)_"

    sources_str = "\n\n".join(
        f"**{k}**:\n```python\n{v[:600]}\n```" for k, v in list(meta["source_samples"].items())[:3]
    ) or "_(no source files sampled)_"

    existing_hint = (
        f"\n\nExisting README to improve:\n```\n{meta['existing_readme'][:2000]}\n```"
        if meta["existing_readme"]
        else ""
    )

    prompt = f"""You are a senior developer and technical writer creating a thorough, impressive GitHub README.
Your output should rival the quality of top open-source projects like FastAPI, Rich, or Pydantic.

Repository: {meta['repo']}
Default branch: {meta['branch']}

CI Badges (include exactly as-is):
{badges_str}

Config / manifest files:
{configs_str}

Source file samples:
{sources_str}

Python modules found:
{', '.join(meta['modules'][:10]) or 'none'}
{existing_hint}

Write a COMPREHENSIVE, DETAILED README.md in Markdown. Include ALL of the following sections,
making each section substantive (not just one-liners). Infer everything you can from the code samples and configs:

1. **Project title** – large H1, with a punchy one-sentence tagline
2. **Badges row** – CI badge + any others you can infer (Python version, license, PyPI)
3. **Short description paragraph** – 3-5 sentences explaining what the project does, why it exists, and who it's for
4. **✨ Features** – detailed bullet list, at least 6-8 points, each with a short explanation (not just a word)
5. **📋 Table of Contents** – linked anchors to every section
6. **🏗️ Architecture / How it works** – a short prose explanation of the system design, mention key components
7. **⚙️ Prerequisites** – list exact requirements (Python version, OS, any system deps)
8. **🚀 Installation** – step-by-step with actual shell commands in code blocks
9. **📖 Quick Start** – a realistic, working usage example with code block showing input and output
10. **🔧 Configuration** – table or list of all config options with types, defaults, and descriptions
11. **📚 API Reference / Usage** – document the main functions/classes/CLI commands with examples
12. **🗂️ Project Structure** – file tree with one-line description of each important file
13. **🧪 Running Tests** – exact commands to run the test suite
14. **🤝 Contributing** – how to fork, branch, commit, PR; mention code style
15. **📄 License** – infer from configs or state MIT as default

Rules:
- Every code block must have a language tag (```python, ```bash, etc.)
- Use tables where comparing options or listing config params
- Use emoji section headers for visual appeal
- Be SPECIFIC – use actual file names, function names, class names from the source samples
- Minimum 600 words total
- Return ONLY the raw Markdown – absolutely no preamble, no explanation, no code fences wrapping the whole thing"""

    logger.info("Asking LLM to generate README ...")
    readme = call_llm(prompt, max_tokens=3000, temperature=0.2)
    return readme.strip()


# ─── PR creation ─────────────────────────────────────────────────────────────

def push_readme_pr(repo: str, new_readme: str, branch: str = "main") -> str:
    """Create a bot branch, push new README, open a PR. Returns PR URL."""
    bot_branch = "readme-bot-update"

    logger.info(f"Creating branch {bot_branch} ...")
    gh.create_branch(repo, bot_branch, from_branch=branch)

    logger.info("Pushing new README ...")
    gh.update_file(
        repo,
        "README.md",
        new_readme,
        "🤖 Auto-generated README by Git-Repo-Caretaker",
        branch=bot_branch,
    )

    logger.info("Opening PR ...")
    pr = gh.create_pr(
        repo,
        title="🤖 Auto-generated README update",
        body=(
            "This PR was automatically created by **Git-Repo-Caretaker**.\n\n"
            "The README was refreshed based on the current source tree, config files, "
            "and CI badges.\n\n"
            "> Please review and merge if the content looks good, or close if not needed."
        ),
        head=bot_branch,
        base=branch,
    )
    return pr.get("html_url", "")


# ─── Main entry point ─────────────────────────────────────────────────────────

SIMILARITY_THRESHOLD = float(os.getenv("README_SIMILARITY_THRESHOLD", "0.85"))


def run(repo: str, branch: str = "main", force: bool = False) -> dict:
    """
    Main README-update routine.
    Returns a dict with status information.
    """
    result = {"repo": repo, "action": "none", "details": ""}

    try:
        meta = extract_repo_metadata(repo, branch)
        new_readme = generate_readme(meta)

        existing = meta.get("existing_readme", "")

        if not force:
            similarity = _levenshtein_ratio(existing, new_readme)
            logger.info(f"Similarity score: {similarity:.2f} (threshold {SIMILARITY_THRESHOLD})")
            if similarity >= SIMILARITY_THRESHOLD:
                result["action"] = "skipped"
                result["details"] = f"README is already {similarity:.0%} similar. No update needed."
                logger.info(result["details"])
                return result

        pr_url = push_readme_pr(repo, new_readme, branch)
        result["action"] = "pr_created"
        result["details"] = f"PR opened: {pr_url}"
        logger.success(result["details"])

    except Exception as e:
        result["action"] = "error"
        result["details"] = str(e)
        logger.error(f"README bot error: {e}")

    return result


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse, json

    parser = argparse.ArgumentParser(description="Auto-generate GitHub README")
    parser.add_argument("repo", help="owner/repo-name")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--force", action="store_true", help="Skip similarity check")
    args = parser.parse_args()

    result = run(args.repo, args.branch, args.force)
    print(json.dumps(result, indent=2))
