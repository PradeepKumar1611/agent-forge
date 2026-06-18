# Agent Factory — Setup & Migration Guide

## Prerequisites

Before you begin, make sure the new machine has:

1. **Python 3.10+**
   ```bash
   python3 --version    # should be 3.10 or higher
   ```

2. **Node.js 18+** (required for Claude Code CLI)
   ```bash
   node --version
   ```

3. **Claude Code CLI**
   ```bash
   npm install -g @anthropic-ai/claude-code
   claude --version     # should print a version number
   ```

---

## Files to Copy

Copy these **14 files** to the new machine (keeping folder structure):

```
agent-factory/
├── server.py              # Main Flask app
├── auth.py                # Login, users, activity logging
├── security.py            # Guardrails and input validation
├── claude_client.py       # Claude Code CLI wrapper
├── generator.py           # Project file generation engine
├── database.py            # SQLite project store
├── requirements.txt       # Python dependencies
├── .env.example           # Environment template
├── .gitignore             # Git ignore rules
├── README.md              # Project documentation
├── templates/
│   ├── index.html         # Main app UI
│   ├── login.html         # Login page
│   └── admin.html         # Admin panel
```

### Optional (if you want to keep existing data)

```
├── agent_factory.db       # Project database (auto-created if missing)
├── users.json             # User accounts (auto-created if missing)
├── .env                   # Your secrets (recreate from .env.example if lost)
├── logs/
│   └── activity.jsonl     # Activity history
└── generated_projects/    # All your generated projects
```

---

## Step-by-Step Setup on New Machine

```bash
# 1. Navigate to the project
cd agent-factory

# 2. Create a virtual environment
python3 -m venv venv

# 3. Activate it
source venv/bin/activate          # Linux / Mac
# venv\Scripts\activate           # Windows

# 4. Install dependencies
pip install -r requirements.txt

# 5. Create your environment file
cp .env.example .env

# 6. (Optional) Edit .env to set your admin password
#    Default is admin123 — change it for security
nano .env

# 7. Start the server
python3 server.py
```

Open **http://localhost:5000** in your browser.

---

## First Login

| Field    | Value                                            |
|----------|--------------------------------------------------|
| Username | `admin`                                          |
| Password | Whatever you set as `ADMIN_PASSWORD` in `.env`   |
|          | Default: `admin123`                              |

After login:
- Click **Change Password** in the header to set a new password
- Click **Admin** to add more users

---

## .env File Reference

```bash
# Default admin password (only used on FIRST startup to create the admin user)
ADMIN_PASSWORD=admin123

# Flask session key (set to a fixed string so sessions survive server restarts)
FLASK_SECRET_KEY=change-me-to-a-random-string
```

---

## Auto-Created Files

These are created automatically on first run — no need to copy them:

| File/Folder            | Created When       | Purpose                           |
|------------------------|--------------------|------------------------------------|
| `agent_factory.db`     | First startup      | SQLite database for all projects   |
| `users.json`           | First startup      | User accounts                     |
| `logs/`                | First startup      | Activity logs directory            |
| `logs/activity.jsonl`  | First user action  | Activity log                      |
| `generated_projects/`  | First project      | Generated project output           |

---

## Troubleshooting

**"Claude Code CLI not found"**
- Make sure `claude` is in PATH: `which claude`
- Install it: `npm install -g @anthropic-ai/claude-code`

**"Address already in use" (port 5000)**
- Another process is using port 5000
- Kill it: `lsof -i :5000` then `kill <PID>`
- Or change the port in `server.py` (last line)

**"ModuleNotFoundError: No module named 'flask'"**
- Make sure venv is activated: `source venv/bin/activate`
- Reinstall: `pip install -r requirements.txt`

**Login not working after server restart**
- If `FLASK_SECRET_KEY` is not set in `.env`, sessions reset on restart
- Set it to a fixed string: `FLASK_SECRET_KEY=my-secret-key-123`

**Projects after restart**
- All projects are persisted in `agent_factory.db` (SQLite) and survive restarts
- Generated project files are also saved on disk in `generated_projects/`
- If you delete `agent_factory.db`, the project list resets but generated files on disk remain

---

## Quick Copy Command

To copy everything in one shot from the current machine:

```bash
# From the current machine — creates a clean tar
cd /home/pradeep-3562/Documents/Claude_Code
tar czf agent-factory-portable.tar.gz \
  --exclude='generated_projects' \
  --exclude='__pycache__' \
  --exclude='venv' \
  --exclude='.playwright-mcp' \
  --exclude='test-screenshots' \
  --exclude='.claude' \
  --exclude='users.json' \
  --exclude='agent_factory.db' \
  --exclude='.env' \
  --exclude='logs' \
  --exclude='*.pyc' \
  agent-factory/

# Copy to new machine
scp agent-factory-portable.tar.gz user@new-machine:~/

# On the new machine
cd ~
tar xzf agent-factory-portable.tar.gz
cd agent-factory
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python3 server.py
```
