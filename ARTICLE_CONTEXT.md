# Agent Factory — Complete Context for Article Writing

Give this entire document to Claude in the browser and ask it to write an article about Agent Factory. You can discuss tone, audience, format, and iterate.

---

## What is Agent Factory?

Agent Factory is a web application that lets anyone design and generate production-ready multi-agent systems powered by Claude Code — without writing a single line of code. You describe what you want in plain English, fill in a form, and get a complete downloadable project with instruction files, a live dashboard, and a one-click launch command.

## The Problem It Solves

Building a multi-agent system with Claude Code requires:
- Writing a CLAUDE.md entry point
- Designing per-agent instruction files with specific procedures
- Setting up a dashboard for monitoring
- Managing configuration and secrets
- Implementing error recovery (RALPH self-healing loops)
- Creating a server for the dashboard

This takes hours of manual work per project. Agent Factory automates the entire process in 3-4 minutes.

## How It Works (User Flow)

1. **Describe** — User types what they want: "Build an agent that monitors website uptime and sends Slack alerts"
2. **Form** — Claude designs a custom input form (URLs to monitor, Slack webhook, check interval, etc.)
3. **Dashboard** — Monitoring metrics are designed (progress bars, error counts, timers)
4. **Generate** — Claude generates detailed instruction files (100+ lines each), CLAUDE.md, dashboard HTML, and all supporting files
5. **Download** — Complete project as ZIP, ready to run

## What Gets Generated (Per Project)

Each generated project is self-contained and includes:

| File | Purpose | Lines |
|------|---------|-------|
| `CLAUDE.md` | Master entry point with RALPH loop, execution order, dashboard state protocol | 130-145 |
| `config.json` | User configuration (populated from the form) | varies |
| `instructions/00_dashboard.md` | Dashboard state management protocol | 90 |
| `instructions/01-05_*.md` | Per-agent instruction files with domain-specific procedures, RALPH retry strategies, verification commands | 125-150 each |
| `dashboard.html` | Live dashboard with 3 tabs: Configure, Dashboard, Report | 350-450 |
| `run_server.py` | Flask server with 7 endpoints (/state, /save-config, /config, /report, /project-dir, /respond, /audit) | 110 |
| `.env` / `.env.example` | Secret management | varies |
| `logs/dashboard_state.json` | Real-time state with nested steps, logs, files_changed, human_intervention | 60 |

## Key Technical Features

### RALPH Self-Healing Loop
Every agent follows RALPH for error recovery:
- **R** — Read the error message carefully
- **A** — Analyze root cause
- **L** — List at least 2 possible fixes
- **P** — Pick the safest fix and apply it
- **H** — Halt and escalate to human if 5 attempts exhausted

Each instruction file has 5 specific retry strategies tailored to that agent's failure modes.

### Generated Dashboard (3 Tabs)
1. **Configure Tab** — Form UI with all input fields, grouped by section. "Save Config & Generate Launch Command" button writes config.json and shows the exact claude command to run.
2. **Dashboard Tab** — Real-time step tracking (pending/running/done/failed), activity log, files changed, flat metrics, human intervention panel.
3. **Report Tab** — Auto-loads the final report (HTML/PDF/Markdown/JSON) when pipeline completes. Download button included.

### Human Intervention Protocol
If an agent exhausts all 5 RALPH attempts:
- Sets `human_intervention.needed = true` in dashboard_state.json
- Dashboard shows an intervention panel with the error details
- User types a response
- Agent reads `human_response.json` and resumes

### Security Guardrails
- Query filtering blocks source code extraction ("show me server.py") and prompt injection ("ignore previous instructions")
- Path traversal protection on all form inputs
- Input size limits (5000 chars description, 3000 chars messages)
- All passwords hashed with SHA-256
- Secrets never exposed in API responses
- Claude prompts hardened with security suffix

### Authentication & User Management
- Session-based login with Flask signed cookies
- Admin panel at `/admin` for adding/deleting users
- Role-based access: Admin (manage users + see all projects) vs User (own projects only)
- Change password from header
- Activity logging to `logs/activity.jsonl` (every action timestamped)
- Auto-generated FLASK_SECRET_KEY persisted to .env (sessions survive restarts)

### Project Management Features
- **SQLite persistence** — Projects survive server restarts
- **Clone** — One-click duplicate a project's design, fill with new values
- **Export/Import** — Share project designs as JSON between team members or instances
- **Tags** — Categorize projects (monitoring, migration, security, etc.) with filtering
- **Public/Private** — Admin can toggle projects public for all users to download
- **Project Browser** — "Projects" button in header shows all projects with search/filter by tag

## Architecture

```
agent-factory/
├── server.py          # Flask app — routes + orchestration (280 lines)
├── auth.py            # Login, users, activity logging (160 lines)
├── security.py        # Guardrails, sanitization, middleware (130 lines)
├── claude_client.py   # Claude Code CLI wrapper + JSON parsing (80 lines)
├── generator.py       # Project file generation engine (1050 lines)
├── database.py        # SQLite project persistence (100 lines)
├── templates/
│   ├── index.html     # Main app UI
│   ├── login.html     # Login page
│   └── admin.html     # Admin panel
├── agent_factory.db   # SQLite database
├── users.json         # User accounts
└── generated_projects/
    ├── website_uptime_monitor_agent/
    ├── db_backup_recovery_pipeline_agent/
    └── ...
```

No frontend build tools. No React/Vue. Vanilla HTML + CSS + JS. Flask backend. SQLite for persistence. Claude Code CLI for AI.

## Tech Stack

- **Backend**: Python 3.10+, Flask
- **Database**: SQLite (built-in, no extra dependency)
- **AI**: Claude Code CLI (subprocess calls)
- **Frontend**: Vanilla HTML/CSS/JS (no build step)
- **Auth**: SHA-256 hashed passwords, Flask sessions
- **Dependencies**: Only `flask` and `python-dotenv`

## Example Projects Generated

These are real projects created and validated during development:

1. **Website Uptime Monitor** — 12 fields, 4 agents, monitors URLs, screenshots failures, Slack alerts
2. **DB Backup Recovery Pipeline** — 27 fields, 4 agents, pg_dump, S3 upload, staging restore, compliance report
3. **K8s Security Scanner** — 14 fields, 5 agents, RBAC, pod security, CIS benchmark compliance
4. **API Performance Tester** — 14 fields, 5 agents, OpenAPI parsing, progressive load tests, p99 latency analysis
5. **React 18 Migration Pipeline** — 16 fields, 5 agents, class→hooks, Router v5→v6, Enzyme→RTL
6. **SSL Certificate Monitor** — 19 fields, 5 agents, expiry checks, cipher validation, Heartbleed/POODLE detection
7. **Infrastructure Drift Detector** — 18 fields, 5 agents, Terraform state vs AWS reality, drift remediation PRs
8. **Code Quality Analyzer** — 13 fields, 5 agents, static analysis, coverage, complexity, duplication
9. **DB Schema Migration (MySQL→PostgreSQL)** — 18 fields, 5 agents, type mapping, batch data transfer, checksums
10. **CI/CD Pipeline Generator** — 14 fields, 5 agents, tech stack detection, GitHub Actions, Docker, deployment workflows

Each project was generated in ~3-4 minutes and validated with 70+ automated checks.

## Reference Projects That Inspired the Design

### BrowserUpgrade (Production, 17+ runs)
- Automates browser version upgrades across a large Java monorepo
- 6 sequential agents with 945 lines of detailed instructions
- Mercurial integration, AI code review via Azure Claude, standalone Java test generation
- Previous upgrade diff as learning source
- 3 known miss-spots documented with verification grep commands

### Network Migration (Production)
- Migrates Java changes from OpManager repos to Site24x7
- Multi-VCS support (Git + Mercurial 3+ GB repo)
- Smart merge engine with Site24x7-specific block preservation
- PMD code quality enforcement
- Migration dashboard with configure page

## Testing Results

### Regression Test: 90/90 PASS
Covers: Auth, Admin, Password, Security (9 checks), Isolation, Tags, Full Project Flow, Generated Files (30 checks), Clone, Export/Import, Download, Persistence, Activity Log

### 10 Complex Projects: 10/10 PASS, 0 issues
Each validated for: config.json, CLAUDE.md quality (10 checks), dashboard HTML (14 checks), run_server.py (7 checks), dashboard state schema (6 checks), instruction files (5 checks per agent), supporting files (8 checks), folder naming

## How to Run

```bash
cd agent-factory
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # edit ADMIN_PASSWORD
python3 server.py     # open http://localhost:5000
```

Default login: admin / admin123

## How a Generated Project Runs

```bash
cd generated_projects/my_project_agent/
pip install -r requirements.txt
python3 run_server.py  # open http://localhost:8080
# Fill Configure tab → Click "Save Config & Generate Launch Command" → Copy command
claude --dangerously-skip-permissions "Read CLAUDE.md and understand the project and execute all the agents in order"
```

## Key Design Decisions

1. **No database ORM** — Projects stored as JSON blobs in SQLite. Simple, no migrations.
2. **Claude generates instructions** — Not templates. Each instruction file is domain-specific with actual bash/python commands.
3. **File-based IPC for dashboards** — Agents write to `dashboard_state.json`, dashboard polls every 2 seconds. No WebSockets needed.
4. **RALPH at every level** — CLAUDE.md, every instruction file, and the dashboard protocol all enforce the self-healing loop.
5. **Configure tab in generated projects** — Users change inputs via UI form, not by editing config.json manually.
6. **Report tab** — Every project asks for report format preference and the final agent generates a viewable report.

## Who Built It

Built for the Site24x7 team to help team members design and generate multi-agent systems without deep Claude Code expertise.

## Numbers

- **6 Python modules** (server, auth, security, claude_client, generator, database)
- **3 HTML templates** (main app, login, admin)
- **~2000 lines** of Python backend
- **~1000 lines** of HTML/CSS/JS frontend
- **17-18 files** generated per project
- **125-150 lines** per instruction file (Claude-generated)
- **350-450 lines** per generated dashboard
- **3-4 minutes** per project generation
- **90+ regression test checks** all passing
- **0 external dependencies** beyond Flask and python-dotenv
