# Agent Forge

A web application that designs and generates production-ready multi-agent systems powered by Claude Code. Describe what you want, fill in a form, and get a complete project with CLAUDE.md, instruction files, a live dashboard, and a launch command.

## Prerequisites

- **Python 3.10+**
- **Claude Code CLI** — must be installed and available as `claude` in PATH
  ```bash
  # Install Claude Code (if not already installed)
  npm install -g @anthropic-ai/claude-code

  # Verify it works
  claude --version
  ```

## Quick Setup (New Machine)

```bash
# 1. Clone or copy the project
cd agent-forge

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate    # Linux/Mac
# venv\Scripts\activate     # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env — change ADMIN_PASSWORD and FLASK_SECRET_KEY

# 5. Start the server
python3 server.py
```

Open **http://localhost:5000** in your browser.

## Default Login

| Field | Value |
|-------|-------|
| Username | `admin` |
| Password | whatever you set in `.env` as `ADMIN_PASSWORD` (default: `admin123`) |

Change the password immediately after first login via **Change Password** in the header.

## Project Structure

```
agent-forge/
├── server.py              # Flask app — routes and orchestration
├── auth.py                # Login, user management, activity logging
├── security.py            # Guardrails, input validation, prompt hardening
├── claude_client.py       # Claude Code CLI wrapper
├── generator.py           # Project file generation engine
├── database.py            # SQLite project persistence
├── requirements.txt       # Python dependencies
├── .env                   # Secrets (never commit)
├── .env.example           # Template for .env
├── agent_factory.db       # SQLite database — projects persist across restarts
├── users.json             # User store (auto-created on first run)
├── templates/
│   ├── index.html         # Main app UI
│   ├── login.html         # Login page
│   └── admin.html         # Admin panel
├── logs/
│   └── activity.jsonl     # User activity log
└── generated_projects/    # Output directory for generated projects
```

## How It Works

1. **Describe** — Tell it what agent you want ("Build a website translator")
2. **Form** — A custom input form is generated (URLs, paths, credentials, report format)
3. **Dashboard** — Monitoring metrics are designed for the pipeline
4. **Generate** — Complete project with CLAUDE.md, instruction files, dashboard, and launch command
5. **Download** — Get a ZIP of the entire project

## Generated Projects Include

Each generated project is self-contained and ready to run:

- `CLAUDE.md` — Master entry point with RALPH self-healing loop
- `config.json` — User configuration (populated from the form)
- `instructions/` — Detailed per-agent instruction files (100+ lines each)
- `dashboard.html` — Live dashboard with Configure tab, Dashboard tab, and Report tab
- `run_server.py` — Dashboard server with `/save-config`, `/report`, `/project-dir` endpoints
- `.env` / `.env.example` — Secret management
- `logs/dashboard_state.json` — Real-time state for dashboard polling

## Running a Generated Project

```bash
cd generated_projects/my_project_agent/

# 1. Install dependencies
pip install -r requirements.txt

# 2. Open the dashboard (separate terminal)
python3 run_server.py
# Open http://localhost:8080

# 3. Fill in the Configure tab and click "Save Config & Generate Launch Command"

# 4. Copy and run the launch command in a terminal
claude --dangerously-skip-permissions "Read CLAUDE.md and understand the project and execute all the agents in order"
```

## User Management

- **Admin** can add/delete users and view activity logs at `/admin`
- **Users** can create projects and change their own password
- Each user only sees their own projects; admins see all
- All actions are logged to `logs/activity.jsonl`

## Security

- Query filtering blocks source code extraction and prompt injection attempts
- Path traversal protection on all form inputs
- Secrets stored in `.env`, never exposed in API responses
- All passwords hashed with SHA-256
- Session-based authentication with signed cookies

## Files to Back Up

When moving to a new machine, copy the entire `agent-forge/` directory. The key files are:

| File | Purpose | Recreatable? |
|------|---------|-------------|
| `*.py` + `templates/` | Application code | No — this IS the app |
| `.env` | Secrets | No — recreate from `.env.example` |
| `agent_factory.db` | All project data (SQLite) | No — your project history |
| `users.json` | User accounts | No — or recreate from admin panel |
| `generated_projects/` | All generated projects | No — your work output |
| `logs/activity.jsonl` | Activity history | Optional — nice to keep |

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ADMIN_PASSWORD` | First run only | `admin123` | Password for the default admin user |
| `FLASK_SECRET_KEY` | Recommended | Random | Session signing key — set to a fixed string so sessions survive restarts |
