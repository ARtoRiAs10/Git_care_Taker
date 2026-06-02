# 🤖 Git-Repo-Caretaker

An **agentic AI layer** that sits on top of your GitHub repo and handles repetitive
maintenance tasks — powered by free LLMs via [OpenRouter](https://openrouter.ai).

| Feature | What it does |
|---|---|
| 📝 **README Bot** | Auto-generates/refreshes README from code, configs, badges |
| 🏷️ **Triage Bot** | Labels new issues, detects duplicates, posts first-response comments |
| ⬆️ **Dependency Bot** | Detects outdated packages and opens update PRs |
| 📋 **Personal Assistant** | Release summaries, daily digests, meeting agendas, notebook backups |

---

## ⚡ Quick Start (5 minutes)

### 1. Get your API keys

| Key | Where to get it |
|---|---|
| `GH_TOKEN` | [github.com/settings/tokens](https://github.com/settings/tokens) – scopes: `repo`, `workflow`, `read:org` |
| `OPENROUTER_API_KEY` | [openrouter.ai/keys](https://openrouter.ai/keys) – free account, no credit card |

### 2. Clone & configure

```bash
git clone https://github.com/yourname/git-repo-caretaker
cd git-repo-caretaker

# Copy and fill in the env file
cp .env.example .env
# Edit .env: set GH_TOKEN and OPENROUTER_API_KEY

# Edit config.yaml: set your repo name and maintainers
nano config.yaml
```

### 3. Install Python dependencies

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Run your first task

```bash
# Refresh the README for your repo
python run.py readme  username/my-repo

# Triage all open issues
python run.py triage  username/my-repo --all

# Check for outdated dependencies (dry run)
python run.py deps    username/my-repo --dry-run

# Generate a meeting agenda
python run.py agenda  username/my-repo
```

---

## 🛠️ All CLI Commands

```
python run.py readme  owner/repo [--branch main] [--force]
python run.py triage  owner/repo --issue 42
python run.py triage  owner/repo --pr 17
python run.py triage  owner/repo --all
python run.py deps    owner/repo [--branch main] [--dry-run]
python run.py digest  owner/repo --assignee alice [--email]
python run.py agenda  owner/repo
python run.py release owner/repo
python run.py backup  --dir ~/notebooks --backup-repo owner/backups
```

---

## 🌐 Running the Webhook Server

The webhook server receives real-time GitHub events (new issue → auto-triage instantly).

```bash
# Start the server
uvicorn app:app --host 0.0.0.0 --port 8000 --reload

# Expose it to GitHub (for local development)
npx localtunnel --port 8000
# or
ngrok http 8000
```

Set the tunnel URL as a webhook in your GitHub repo:
> Settings → Webhooks → Add webhook
> Payload URL: `https://your-tunnel-url/webhook`
> Content type: `application/json`
> Secret: same value as `GITHUB_WEBHOOK_SECRET` in `.env`
> Events: Issues, Pull requests, Pushes, Releases

### API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `POST` | `/webhook` | GitHub event receiver |
| `POST` | `/run/readme?force=false` | Trigger README update |
| `POST` | `/run/triage/{number}?kind=issue` | Triage single issue/PR |
| `POST` | `/run/triage-all` | Triage all open issues |
| `POST` | `/run/deps?dry_run=false` | Run dependency check |
| `GET` | `/run/digest?assignee=alice` | Generate to-do digest |
| `GET` | `/run/agenda` | Generate meeting agenda |
| `GET` | `/run/release-summary` | Generate release summary |

---

## 🐳 Docker Deployment

```bash
# Build and start both webhook server + scheduler
cp .env.example .env        # fill in your keys
docker compose up -d

# View logs
docker compose logs -f

# Stop
docker compose down
```

---

## ⚙️ GitHub Actions (no server needed)

The `.github/workflows/` directory contains three workflows that run entirely
inside GitHub's free CI — no server required:

| Workflow | Trigger | What it does |
|---|---|---|
| `readme_update.yml` | Daily 06:00 UTC + every push | Refreshes README |
| `dependency_check.yml` | Every Monday 09:00 UTC | Opens dep-update PRs |
| `triage.yml` | New issue / new PR | Labels + comments |

**To enable them in your own repo:**

```bash
# Copy the workflows into your target repo
cp -r .github/workflows/ /path/to/your-repo/.github/workflows/
```

Then add these **repository secrets** (Settings → Secrets → Actions):
- `GH_TOKEN` – your GitHub token
- `OPENROUTER_API_KEY` – your OpenRouter key

---

## 🧠 Free LLM Models (OpenRouter)

The project defaults to `meta-llama/llama-3.3-70b-instruct:free`.
Edit `OPENROUTER_MODEL` in `.env` or `config.yaml` to switch:

| Model | Quality | Notes |
|---|---|---|
| `meta-llama/llama-3.3-70b-instruct:free` | ⭐⭐⭐⭐ | Best free option |
| `google/gemma-3-27b-it:free` | ⭐⭐⭐⭐ | Fast, good JSON |
| `mistralai/mistral-7b-instruct:free` | ⭐⭐⭐ | Lightweight |
| `meta-llama/llama-3.1-8b-instruct:free` | ⭐⭐ | Fastest |

The client automatically falls back through this list if a model is unavailable.

---

## 🧪 Tests

```bash
pytest tests/ -v
```

---

## 📁 Project Structure

```
git-repo-caretaker/
├── app.py                  # FastAPI webhook server
├── run.py                  # CLI entry point
├── scheduler.py            # APScheduler cron runner
├── llm_client.py           # OpenRouter LLM wrapper (with fallback)
├── github_client.py        # GitHub REST API wrapper
├── readme_bot.py           # README auto-generator
├── triage_bot.py           # Issue & PR triage
├── dependency_bot.py       # Dependency update checker
├── personal_assistant.py   # Release notes, digests, backups, agendas
├── config.yaml             # Main configuration
├── .env.example            # Environment variable template
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── tests/
│   └── test_all.py
└── .github/
    └── workflows/
        ├── readme_update.yml
        ├── dependency_check.yml
        └── triage.yml
```

---

## 🔒 Security Notes

- **Never** commit `.env` to git — it's in `.gitignore`.
- Use a **fine-grained GitHub token** scoped to just the target repo.
- Set `GITHUB_WEBHOOK_SECRET` so the server rejects unsigned payloads.
- Run the Docker container as a non-root user (already configured).

---

## 📄 License

MIT — do whatever you want with it.
