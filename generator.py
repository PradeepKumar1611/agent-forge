"""Project file generator for Agent Factory.

Generates all project files: CLAUDE.md, config.json, instruction files,
dashboard HTML with configure form, run_server.py, and supporting files.
"""
import json
import re
import stat
from pathlib import Path

from claude_client import run_claude_code, extract_json_from_text


def _skill_folder_name(name):
    """Convert a skill name to a clean folder name (no numbered prefix)."""
    return re.sub(r"[^a-z0-9_]", "_", name.lower().strip()).strip("_") or "skill"


def generate_project_files(project, project_id, projects_dir):
    """Generate all project files. Returns (generated_files, project_dir)."""
    safe_name = re.sub(r"[^a-z0-9_]", "_", project["name"].lower().strip()).strip("_")
    if not safe_name:
        safe_name = "agent"
    # Use clean project name as folder, append project_id suffix only if name already exists
    project_dir = projects_dir / f"{safe_name}_agent"
    if project_dir.exists():
        project_dir = projects_dir / f"{safe_name}_agent_{project_id[:6]}"
    skills_dir = project_dir / "skills"
    logs_dir = project_dir / "logs"
    agents_dir = project_dir / "agents"
    skills_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    agents_dir.mkdir(parents=True, exist_ok=True)

    generated_files = []
    # Build the complete config from form values
    form_values = project.get("form_values", {})
    secret_field_ids = {f["id"] for f in project.get("form_fields", []) if f.get("is_secret")}

    # Generate config.json
    config_data = {}
    for field in project.get("form_fields", []):
        fid = field["id"]
        if fid in secret_field_ids:
            config_data[fid] = f"${{ENV:{fid.upper()}}}"
        else:
            config_data[fid] = form_values.get(fid, "")
    (project_dir / "config.json").write_text(json.dumps(config_data, indent=2))
    generated_files.append("config.json")

    # Build human-readable config summary
    config_summary_lines = []
    for field in project.get("form_fields", []):
        fid = field["id"]
        label = field.get("label", fid)
        if fid in secret_field_ids:
            config_summary_lines.append(f"- **{label}**: Read from environment variable `{fid.upper()}` (set in .env)")
        else:
            val = form_values.get(fid, "")
            if isinstance(val, bool):
                val = "Yes" if val else "No"
            display = val if val else "(not set)"
            config_summary_lines.append(f"- **{label}**: `{display}`")
    config_summary = "\n".join(config_summary_lines) if config_summary_lines else "- No configuration provided"

    # Build per-agent metric mapping
    dashboard_metrics = project.get("dashboard_metrics", [])
    sub_agents = project.get("skills") or project.get("sub_agents", [])

    # Build step-based dashboard state (like reference projects)
    steps_state = {}
    for idx, agent in enumerate(sub_agents):
        steps_state[str(idx + 1)] = {
            "status": "pending",
            "title": agent.get("name", f"Step {idx+1}"),
            "subtitle": "Waiting to start",
            "time": "0:00",
            "retries": 0,
        }

    initial_state = {
        "overall_status": "idle",
        "started_at": None,
        "finished_at": None,
        "total_retries": 0,
        "steps": steps_state,
        "files_changed": [],
        "logs": [{"ts": "--:--:--", "step": "INIT", "level": "INFO", "message": "Dashboard initialized. Waiting for agent to start."}],
        "human_intervention": {"needed": False},
    }
    # Also add flat metrics for backward compat
    for m in dashboard_metrics:
        mtype = m.get("type", "counter")
        mid = m.get("id", "metric")
        if mtype in ("progress_bar", "percentage"):
            initial_state[mid] = 0
        elif mtype in ("counter", "error_count"):
            initial_state[mid] = 0
        elif mtype == "log_stream":
            initial_state[mid] = ["Waiting to start..."]
        elif mtype == "status_badge":
            initial_state[mid] = "idle"
        elif mtype == "timer":
            initial_state[mid] = "00:00"
        else:
            initial_state[mid] = "--"

    (logs_dir / "dashboard_state.json").write_text(json.dumps(initial_state, indent=2))
    generated_files.append("logs/dashboard_state.json")

    # Generate .env
    env_lines = ["# ============================================================",
                 "# .env — ALL secrets live here. NEVER commit this file.",
                 "# ============================================================"]
    for field in project.get("form_fields", []):
        if field.get("is_secret"):
            val = project.get("secret_values", {}).get(field["id"], "")
            env_lines.append(f"# {field.get('label', field['id'])}")
            env_lines.append(f"{field['id'].upper()}={val}")
    env_content = "\n".join(env_lines) + "\n" if any(f.get("is_secret") for f in project.get("form_fields", [])) else "# No secrets configured\n"
    env_path = project_dir / ".env"
    env_path.write_text(env_content)
    env_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    generated_files.append(".env")

    # Generate .env.example
    env_example_lines = ["# Copy this to .env and fill in your credentials"]
    for field in project.get("form_fields", []):
        if field.get("is_secret"):
            env_example_lines.append(f"{field['id'].upper()}=your_{field['id']}_here")
    (project_dir / ".env.example").write_text("\n".join(env_example_lines) + "\n")
    generated_files.append(".env.example")

    # ── Call Claude Code to generate DETAILED instruction files ──
    # This is the key difference: instead of templates, Claude generates
    # domain-specific, deeply engineered instructions for each agent.
    agents_context = json.dumps(sub_agents, indent=2)
    metrics_context = json.dumps(dashboard_metrics, indent=2)
    fields_context = json.dumps(project.get("form_fields", []), indent=2)

    instructions_prompt = f"""You are generating detailed instruction files for a multi-agent system called "{project['name']}".

Project description: {project.get('description', '')}

Sub-agents (in execution order):
{agents_context}

Dashboard metrics:
{metrics_context}

Form fields (user configuration):
{fields_context}

Config values provided by user:
{json.dumps(form_values, indent=2)}

For EACH agent, generate a deeply detailed instruction file. Each file MUST include ALL of these sections:

1. **Title and Description** — What this agent does in 3-5 sentences
2. **Pre-conditions** — What must be true before this agent runs (previous agent output, files that must exist, etc.)
3. **Read Configuration** — MUST read ../config.json at runtime for ALL input values. The user can change config via the dashboard UI at any time. NEVER hardcode values from the generation-time defaults. Always load config.json fresh.
4. **Detailed Procedure** — Step-by-step with ACTUAL bash/python commands, not pseudocode. All file paths, URLs, and options MUST come from config.json — never hardcoded. Include:
   - File paths to read/write (from config.json values)
   - Commands to run (grep, curl, python, etc.)
   - Expected output format
   - Verification commands after each major step
5. **RALPH Self-Healing Loop** — 5 SPECIFIC retry strategies tailored to THIS agent's failure modes. Each attempt must be a DIFFERENT approach. Examples:
   - Attempt 1: Retry same command (transient error)
   - Attempt 2: Try alternative tool/method
   - Attempt 3: Reduce scope (skip non-critical items)
   - Attempt 4: Check prerequisites and fix them
   - Attempt 5: Log detailed error and escalate to human via dashboard_state.json
6. **Dashboard State Updates** — Exact JSON keys to update, with python code showing HOW to update both the step status AND the flat metrics. Include the step number for this agent.
7. **Handoff** — What to pass to the next agent (or final summary if last agent)
8. **Known Pitfalls** — Common failure scenarios specific to this domain

Respond with ONLY a JSON object where keys are instruction filenames and values are the full markdown content. Example:
{{"01_agent_name.md": "# Agent Name\\n\\n## Description\\n...(100+ lines)...", "02_next_agent.md": "..."}}

Each instruction file MUST be at least 80 lines of detailed, actionable content. NOT generic templates.
Respond with ONLY the JSON object."""

    instructions_result = run_claude_code(instructions_prompt, project_id)
    instructions_text = instructions_result.get("text", "")

    # Parse the generated instructions
    generated_instructions = {}
    try:
        generated_instructions = json.loads(instructions_text)
    except json.JSONDecodeError:
        parsed = extract_json_from_text(instructions_text)
        if parsed:
            generated_instructions = parsed

    # Write skill files in folder/SKILL.md format with YAML frontmatter
    for idx, agent in enumerate(sub_agents):
        agent_name = agent.get("name", "Agent")
        old_filename = agent.get("skill_file") or agent.get("instruction_file") or f"{agent_name.lower().replace(' ', '_')}.md"

        # Create skill folder: skills/01_scanner/ with SKILL.md inside
        folder_name = _skill_folder_name(agent_name)
        skill_folder = skills_dir / folder_name
        skill_folder.mkdir(parents=True, exist_ok=True)

        # YAML frontmatter for the skill
        skill_description = agent.get("description", "")
        frontmatter = f"""---
name: {folder_name}
description: {skill_description[:200]}
---

"""

        if old_filename in generated_instructions and len(generated_instructions[old_filename]) > 200:
            # Use Claude-generated detailed content, prepend frontmatter
            content = generated_instructions[old_filename]
            if not content.startswith("---"):
                content = frontmatter + content
            (skill_folder / "SKILL.md").write_text(content)
        else:
            # Enhanced fallback with richer structure
            if idx < len(sub_agents) - 1:
                next_agent = sub_agents[idx + 1]
                next_folder = _skill_folder_name(next_agent.get('name', 'skill'))
                handoff_note = f"Hand off to **{next_agent.get('name', 'next skill')}** (`skills/{next_folder}/SKILL.md`)"
            else:
                handoff_note = "This is the **final agent**. Write completion status and summary to dashboard state."

            fallback_md = f"""# {agent_name}

## Description
{agent.get('description', 'Sub-agent task.')}

## Pre-conditions
- Previous agent(s) must have completed successfully (check `../logs/dashboard_state.json` steps)
- `../config.json` must exist with valid configuration
- `../.env` must exist with required credentials

## Read Configuration

```python
import json, os
from pathlib import Path
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent.parent
load_dotenv(BASE / ".env")
config = json.loads((BASE / "config.json").read_text())

# Access config values:
{chr(10).join(f'# {f.get("label", f["id"])}: config["{f["id"]}"]' for f in project.get("form_fields", []) if f["id"] not in secret_field_ids)}

# Access secrets from environment:
{chr(10).join(f'# {f.get("label", f["id"])}: os.environ.get("{f["id"].upper()}")' for f in project.get("form_fields", []) if f["id"] in secret_field_ids)}
```

## Configuration Available
**Always read `../config.json` at runtime — these are only generation-time defaults:**
{config_summary}

## Detailed Procedure

### Step 1: Initialize
- Read `../config.json` and parse all needed fields
- Update dashboard state: set step {idx+1} to "running"

### Step 2: Execute Core Task
{agent.get('description', 'Execute the agent task.')}
- Use the configuration values from config.json
- Log progress to dashboard state after each major action

### Step 3: Verify Results
- Verify the output is correct and complete
- Run any applicable validation checks

### Step 4: Finalize
- Update dashboard state with final metrics
- Write completion status

## RALPH Self-Healing Loop (5 Attempts)

**Attempt 1** — Retry same operation (transient network/IO error)
**Attempt 2** — Try alternative approach (different tool, different method)
**Attempt 3** — Reduce scope (skip non-critical items, process what's available)
**Attempt 4** — Check and fix prerequisites (missing files, wrong permissions, missing dependencies)
**Attempt 5** — Log detailed error and escalate to human:
```python
state = json.loads((BASE / "logs/dashboard_state.json").read_text())
state["human_intervention"] = {{
    "needed": True,
    "title": "Step {idx+1} Failed: {agent_name}",
    "message": "Describe the exact error here",
    "step": {idx+1},
    "timestamp": datetime.now().isoformat()
}}
state["steps"]["{idx+1}"]["status"] = "failed"
(BASE / "logs/dashboard_state.json").write_text(json.dumps(state, indent=2))
```

## Dashboard State Updates

Update BOTH the step status AND flat metrics:

```python
import json
from datetime import datetime

state = json.loads(open("../logs/dashboard_state.json").read())

# Update step status
state["steps"]["{idx+1}"]["status"] = "running"  # or "done", "failed"
state["steps"]["{idx+1}"]["subtitle"] = "Processing..."
state["steps"]["{idx+1}"]["time"] = "1:30"

# Append to logs
state["logs"].append({{
    "ts": datetime.now().strftime("%H:%M:%S"),
    "step": "STEP {idx+1}",
    "level": "INFO",  # INFO, WARN, ERROR, RETRY, SUCCESS
    "message": "Description of what happened"
}})

# Update flat metrics
{chr(10).join(f'# state["{m.get("id", "")}"] = value  # {m.get("label", "")}' for m in dashboard_metrics)}

open("../logs/dashboard_state.json", "w").write(json.dumps(state, indent=2))
```

## Handoff
{handoff_note}
"""
            (skill_folder / "SKILL.md").write_text(frontmatter + fallback_md)
        generated_files.append(f"skills/{folder_name}/SKILL.md")

    # Generate 00_dashboard.md — dashboard state management protocol
    dashboard_protocol = f"""# Dashboard State Management — Step 0

## Overview
This file defines how ALL agents communicate with the live dashboard via `logs/dashboard_state.json`.
The dashboard polls this file every 2 seconds. Every agent MUST update it after EVERY meaningful action.

## State Schema

```json
{{
  "overall_status": "idle|running|success|failed|warning",
  "started_at": "ISO timestamp or null",
  "finished_at": "ISO timestamp or null",
  "total_retries": 0,
  "steps": {{
    "1": {{ "status": "pending|running|done|failed|warning", "title": "Step Name", "subtitle": "Human-readable status", "time": "M:SS", "retries": 0 }},
    ...
  }},
  "files_changed": [
    {{ "path": "relative/path", "type": "new|edit|delete", "time": "HH:MM" }}
  ],
  "logs": [
    {{ "ts": "HH:MM:SS", "step": "STEP N", "level": "INFO|WARN|ERROR|RETRY|SUCCESS", "message": "..." }}
  ],
  "human_intervention": {{
    "needed": true|false,
    "title": "Short title",
    "message": "Detailed message",
    "step": 1,
    "timestamp": "ISO"
  }}
}}
```

## Log Levels
- **INFO**: Normal progress (e.g., "Processing file X")
- **WARN**: Unexpected but handled (e.g., "File not found, skipping")
- **ERROR**: Something broke (even if retrying)
- **RETRY**: RALPH attempt (e.g., "Attempt 2/5: trying alternative method")
- **SUCCESS**: Step or pipeline completed successfully

## Update Frequency
- After EVERY file processed, command run, or decision made
- On EVERY retry attempt
- On step start and step completion
- The dashboard polls every 2 seconds — updates appear in near-real-time

## Human Intervention Protocol
If an agent exhausts all 5 RALPH attempts:
1. Set `human_intervention.needed = true` with title, message, step
2. Set the step status to "failed"
3. Write to `logs/dashboard_state.json`
4. The dashboard will display an intervention panel
5. Poll `logs/human_response.json` every 5 seconds for a response
6. When response arrives, read it, clear `human_intervention.needed`, and resume

## Python Helper

```python
import json
from datetime import datetime
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent.parent / "logs" / "dashboard_state.json"

def update_state(updates_fn):
    \"\"\"Safely read-modify-write dashboard state.\"\"\"
    state = json.loads(STATE_FILE.read_text())
    updates_fn(state)
    STATE_FILE.write_text(json.dumps(state, indent=2))

def log_step(step_num, level, message):
    \"\"\"Add a log entry.\"\"\"
    def _update(state):
        state["logs"].append({{
            "ts": datetime.now().strftime("%H:%M:%S"),
            "step": f"STEP {{step_num}}",
            "level": level,
            "message": message
        }})
    update_state(_update)

def set_step_status(step_num, status, subtitle=""):
    \"\"\"Update a step's status.\"\"\"
    def _update(state):
        state["steps"][str(step_num)]["status"] = status
        if subtitle:
            state["steps"][str(step_num)]["subtitle"] = subtitle
    update_state(_update)
```
"""
    dash_skill_dir = skills_dir / "00_dashboard"
    dash_skill_dir.mkdir(parents=True, exist_ok=True)
    dash_frontmatter = """---
name: 00_dashboard
description: Dashboard state management protocol. Read this FIRST before executing any other skill. Defines how to update logs/dashboard_state.json for real-time monitoring.
---

"""
    (dash_skill_dir / "SKILL.md").write_text(dash_frontmatter + dashboard_protocol)
    generated_files.append("skills/00_dashboard/SKILL.md")
    # Generate CLAUDE.md (deeply detailed, referencing all files)
    agents_list = "\n".join(
        f"  {idx+1}. **{a.get('name', 'Skill')}** → `skills/{_skill_folder_name(a.get('name', 'skill'))}/SKILL.md`"
        for idx, a in enumerate(sub_agents)
    )
    claude_md = f"""# {project['name']} — Multi-Agent System

## Mission
{project.get('description', 'A multi-agent system.')}

## Configuration

**IMPORTANT**: Always read `config.json` at runtime for ALL input values. The user may change values via the dashboard's Configure tab at any time. NEVER use the defaults listed below — they are only for reference. `config.json` is the ONLY source of truth.

Secrets are in `.env`.

**Default values at generation time** (may have been changed — always read config.json instead):
{config_summary}

**CRITICAL**: Before executing ANY skill, read `config.json` and use the values from there. Do NOT copy values from this file.

## Execution Order

  0. **Dashboard Protocol** → `skills/00_dashboard/SKILL.md` (read first — defines state schema)
{agents_list}

## Workflow
1. Read `config.json` to load all user-provided inputs
2. Read `skills/00_dashboard/SKILL.md` to understand the dashboard state protocol
3. Execute each agent's instruction file **in numbered order**
4. After EVERY action, update `logs/dashboard_state.json` (the dashboard polls it every 2s)
5. On completion, set `overall_status` to "success" and `finished_at` to current timestamp

## RALPH Self-Healing Loop

Every agent follows RALPH for each major step:

```
R — Read the error message and current state carefully
A — Analyze root cause (missing file? auth failure? bad input? network?)
L — List at least 2 possible fixes
P — Pick the safest fix and apply it
H — Halt and escalate to human if 5 attempts exhausted
```

**Max retries: 5 per step.** Each retry MUST use a DIFFERENT strategy.
After 5 failures: write human intervention request to `logs/dashboard_state.json`.

## Dashboard State Updates

After every meaningful action, update `logs/dashboard_state.json`:

```python
import json
from datetime import datetime

state = json.loads(open("logs/dashboard_state.json").read())

# Set overall status
state["overall_status"] = "running"
state["started_at"] = datetime.now().isoformat()

# Update step N
state["steps"]["1"]["status"] = "running"  # pending|running|done|failed
state["steps"]["1"]["subtitle"] = "Processing files..."
state["steps"]["1"]["time"] = "2:30"

# Add log entry
state["logs"].append({{
    "ts": datetime.now().strftime("%H:%M:%S"),
    "step": "STEP 1",
    "level": "INFO",    # INFO|WARN|ERROR|RETRY|SUCCESS
    "message": "Started processing"
}})

# Track files changed
state["files_changed"].append({{
    "path": "output/result.json",
    "type": "new",       # new|edit|delete
    "time": datetime.now().strftime("%H:%M")
}})

open("logs/dashboard_state.json", "w").write(json.dumps(state, indent=2))
```

## Human Intervention

If an agent cannot proceed after 5 RALPH attempts:

```python
state["human_intervention"] = {{
    "needed": True,
    "title": "Step N Failed: Description",
    "message": "Detailed error message and what was tried",
    "step": N,
    "timestamp": datetime.now().isoformat()
}}
```

The dashboard will show an intervention panel. Poll `logs/human_response.json` every 5 seconds for response.

## Running (YOLO Mode — Fully Autonomous)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Review config.json and .env
cat config.json   # verify paths/URLs
cat .env.example  # copy to .env and fill secrets

# 3. Start the dashboard server (separate terminal)
python3 run_server.py
# Open http://localhost:8080

# 4. Run the agent
claude --dangerously-skip-permissions "Read CLAUDE.md and understand the project and execute all the agents in order"
```

## Files
- `CLAUDE.md` — Master entry point (this file)
- `config.json` — User configuration (paths, URLs, options)
- `.env` / `.env.example` — Secrets (never commit .env)
- `skills/00_dashboard/SKILL.md` — Dashboard state protocol
- `skills/<skill_name>/SKILL.md` — Per-skill files (each in its own folder with YAML frontmatter)
- `dashboard.html` — Live monitoring dashboard
- `run_server.py` — Dashboard HTTP server (port 8080)
- `logs/dashboard_state.json` — Live state file (polled every 2s)
- `logs/audit.log` — Full timestamped audit trail
"""
    # Write platform-specific entry points
    target_platform = project.get("target_platform", "claude_code")

    # Always generate CLAUDE.md (works as universal reference)
    (project_dir / "CLAUDE.md").write_text(claude_md)
    generated_files.append("CLAUDE.md")

    # Generate platform-specific entry point if not Claude Code
    if target_platform == "cursor":
        cursorrules = f"""# {project['name']} — Cursor Rules

## Project Context
{project.get('description', '')}

## Configuration
Read `config.json` for all user-provided inputs. Secrets are in `.env`.

## Skills (execute in order)
{chr(10).join(f'{idx+1}. Read and execute `skills/{_skill_folder_name(a.get("name", "skill"))}/SKILL.md` — {a.get("name", "")}' for idx, a in enumerate(sub_agents))}

## Rules
- Always read `skills/00_dashboard/SKILL.md` first to understand the dashboard state protocol
- After every action, update `logs/dashboard_state.json`
- Follow the RALPH self-healing loop (Read, Analyze, List fixes, Pick safest, Halt if stuck)
- Read `config.json` for ALL inputs — never hardcode values
- On completion, set overall_status to "success" in dashboard_state.json
"""
        (project_dir / ".cursorrules").write_text(cursorrules)
        generated_files.append(".cursorrules")

    elif target_platform == "codex":
        agents_md = f"""# {project['name']} — Agent Instructions

## Mission
{project.get('description', '')}

## Configuration
Read `config.json` for all user-provided inputs. Secrets are in `.env`.

## Skills (execute in order)
{chr(10).join(f'{idx+1}. `skills/{_skill_folder_name(a.get("name", "skill"))}/SKILL.md` — {a.get("name", "")}: {a.get("description", "")}' for idx, a in enumerate(sub_agents))}

## Execution
1. Read `config.json` to load inputs
2. Read `skills/00_dashboard/SKILL.md` for the dashboard state protocol
3. Execute each skill file in numbered order
4. Update `logs/dashboard_state.json` after every action
5. On completion, set overall_status to "success"

## Error Recovery (RALPH Loop)
If a step fails, retry up to 5 times with different strategies before escalating.
"""
        (project_dir / "AGENTS.md").write_text(agents_md)
        generated_files.append("AGENTS.md")

    elif target_platform == "generic":
        instructions_md = f"""# {project['name']} — Instructions for Any AI Tool

## What This Project Does
{project.get('description', '')}

## How to Run

1. Open this project in your AI tool (Claude Code, Cursor, Codex, ChatGPT, Copilot, etc.)
2. Ask the AI to read `config.json` and this file
3. Execute each skill file in order:

{chr(10).join(f'   {idx+1}. `skills/{_skill_folder_name(a.get("name", "skill"))}/SKILL.md` — {a.get("name", "")}' for idx, a in enumerate(sub_agents))}

4. Monitor progress in `logs/dashboard_state.json`

## Configuration
All inputs are in `config.json`. Secrets are in `.env`.

## Dashboard
Run `python3 run_server.py` and open http://localhost:8080 to monitor progress.

## Skills Reference
Each skill file in the `skills/` directory is a standalone unit of work.
They are platform-agnostic and work with any AI tool that can read Markdown.
"""
        (project_dir / "INSTRUCTIONS.md").write_text(instructions_md)
        generated_files.append("INSTRUCTIONS.md")

    # Generate requirements.txt
    req_content = "flask\npython-dotenv\nrequests\n"
    tools = project.get("tools_needed", [])
    tool_packages = {
        "playwright": "playwright", "selenium": "selenium", "docker": "docker",
        "beautifulsoup": "beautifulsoup4", "bs4": "beautifulsoup4",
        "pandas": "pandas", "numpy": "numpy", "pytest": "pytest",
    }
    for tool in tools:
        pkg = tool_packages.get(tool.lower())
        if pkg:
            req_content += f"{pkg}\n"
    (project_dir / "requirements.txt").write_text(req_content)
    generated_files.append("requirements.txt")

    # Generate setup_instructions.txt
    setup = f"""# Setup Instructions for {project['name']}

1. Navigate to this directory

2. Create a virtual environment:
   python3 -m venv venv && source venv/bin/activate

3. Install dependencies:
   pip install -r requirements.txt

4. Review config.json — verify target paths, URLs, and options

5. Configure .env:
   cp .env.example .env
   # Edit .env with your actual credentials

6. Start the dashboard (separate terminal):
   python3 run_server.py
   # Open http://localhost:8080

7. Run the agent (YOLO mode):
   claude --dangerously-skip-permissions "Read CLAUDE.md and understand the project and execute all the agents in order"
"""
    (project_dir / "setup_instructions.txt").write_text(setup)
    generated_files.append("setup_instructions.txt")

    # Generate enhanced dashboard.html with step tracking + configure form
    steps_html = ""
    for idx, agent in enumerate(sub_agents):
        steps_html += f"""
        <div class="step-card" id="step-card-{idx+1}">
          <div class="step-num">{idx+1}</div>
          <div class="step-info">
            <div class="step-title">{agent.get('name', f'Step {idx+1}')}</div>
            <div class="step-subtitle" id="step-sub-{idx+1}">Waiting to start</div>
          </div>
          <div class="step-meta">
            <span class="step-time" id="step-time-{idx+1}">--</span>
            <span class="step-status" id="step-status-{idx+1}">○</span>
          </div>
        </div>"""

    metrics_html_cards = ""
    for m in dashboard_metrics:
        mtype = m.get("type", "counter")
        mid = m.get("id", "metric")
        mlabel = m.get("label", "Metric")
        if mtype == "progress_bar":
            metrics_html_cards += f'<div class="metric-card"><div class="metric-label">{mlabel}</div><div class="progress-bar"><div class="progress-fill" id="{mid}" style="width:0%"></div></div><div class="metric-value" id="{mid}_val">0%</div></div>'
        elif mtype == "log_stream":
            pass  # We have a dedicated log section
        else:
            metrics_html_cards += f'<div class="metric-card"><div class="metric-label">{mlabel}</div><div class="metric-value" id="{mid}">--</div></div>'

    # Build the configure form from form_fields
    form_fields_html = ""
    current_group = ""
    for field in project.get("form_fields", []):
        fid = field["id"]
        label = field.get("label", fid)
        ftype = field.get("type", "text")
        placeholder = field.get("placeholder", "")
        desc = field.get("description", "")
        required = field.get("required", False)
        is_secret = field.get("is_secret", False)
        group = field.get("group", "")

        if group and group != current_group:
            if current_group:
                form_fields_html += "</div>"
            form_fields_html += f'<div class="form-section"><div class="form-section-title">{group}</div>'
            current_group = group

        req_badge = ' <span style="color:#f87171">*</span>' if required else ""
        secret_badge = ' <span class="secret-badge">🔒</span>' if is_secret else ""

        if ftype == "checkbox":
            form_fields_html += f'''
            <div class="form-field">
              <label class="checkbox-label"><input type="checkbox" id="cfg_{fid}" class="cfg-input" data-field="{fid}"> {label}{secret_badge}</label>
              <div class="form-hint">{desc}</div>
            </div>'''
        elif ftype == "select":
            opts = field.get("options", [])
            if isinstance(opts, list) and opts:
                options_html = "".join(f'<option value="{o}">{o}</option>' for o in opts)
            else:
                options_html = f'<option value="">{placeholder or "Select..."}</option>'
            form_fields_html += f'''
            <div class="form-field">
              <label>{label}{req_badge}{secret_badge}</label>
              <select id="cfg_{fid}" class="cfg-input form-input" data-field="{fid}">{options_html}</select>
              <div class="form-hint">{desc}</div>
            </div>'''
        elif ftype == "textarea":
            form_fields_html += f'''
            <div class="form-field">
              <label>{label}{req_badge}{secret_badge}</label>
              <textarea id="cfg_{fid}" class="cfg-input form-input" data-field="{fid}" placeholder="{placeholder}" rows="3">{config_data.get(fid, "")}</textarea>
              <div class="form-hint">{desc}</div>
            </div>'''
        else:
            input_type = "password" if is_secret else ("url" if ftype == "url" else ("number" if ftype == "number" else "text"))
            val = config_data.get(fid, "")
            if is_secret:
                val = ""  # Don't pre-fill secrets in HTML
            form_fields_html += f'''
            <div class="form-field">
              <label>{label}{req_badge}{secret_badge}</label>
              <input type="{input_type}" id="cfg_{fid}" class="cfg-input form-input" data-field="{fid}" placeholder="{placeholder}" value="{val}">
              <div class="form-hint">{desc}</div>
            </div>'''

    if current_group:
        form_fields_html += "</div>"

    # Build a JSON list of secret field IDs for the JS to handle .env separately
    secret_ids_json = json.dumps([f["id"] for f in project.get("form_fields", []) if f.get("is_secret")])

    dashboard_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{project['name']} — Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0a0b10;color:#e2e8f0;font-family:'Inter',sans-serif;padding:24px}}
h1{{text-align:center;margin-bottom:8px;color:#4f7df8;font-size:24px;font-weight:700}}
.subtitle{{text-align:center;color:#64748b;margin-bottom:16px;font-size:14px}}
/* Tabs */
.tabs{{display:flex;justify-content:center;gap:4px;margin-bottom:24px}}
.tab{{padding:8px 24px;border-radius:8px;cursor:pointer;font-size:14px;font-weight:500;color:#94a3b8;background:#12131a;border:1px solid rgba(255,255,255,0.06);transition:all .2s}}
.tab:hover{{color:#e2e8f0;border-color:rgba(255,255,255,0.1)}}
.tab.active{{color:#4f7df8;border-color:#4f7df8;background:rgba(79,125,248,0.08)}}
.tab-page{{display:none}}.tab-page.active{{display:block}}
/* Overall badge */
.overall-badge{{text-align:center;margin-bottom:24px}}
.badge{{display:inline-block;padding:6px 16px;border-radius:20px;font-size:13px;font-weight:600;text-transform:uppercase;letter-spacing:1px}}
.badge-idle{{background:rgba(100,116,139,0.2);color:#94a3b8}}
.badge-running{{background:rgba(251,191,36,0.2);color:#fbbf24}}
.badge-success{{background:rgba(52,211,153,0.2);color:#34d399}}
.badge-failed{{background:rgba(248,113,113,0.2);color:#f87171}}
/* Layout */
.layout{{display:grid;grid-template-columns:340px 1fr;gap:20px;max-width:1400px;margin:0 auto}}
.panel{{background:#12131a;border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:20px}}
.panel-title{{font-size:12px;text-transform:uppercase;color:#64748b;letter-spacing:1px;margin-bottom:16px;font-weight:600}}
/* Steps */
.step-card{{display:flex;align-items:center;gap:12px;padding:12px;border-radius:8px;margin-bottom:8px;background:#161822;border:1px solid rgba(255,255,255,0.04)}}
.step-card.active{{border-color:#4f7df8;background:rgba(79,125,248,0.08)}}
.step-card.done{{border-color:#34d399;background:rgba(52,211,153,0.05)}}
.step-card.failed{{border-color:#f87171;background:rgba(248,113,113,0.05)}}
.step-num{{width:28px;height:28px;border-radius:50%;background:#1e2030;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:600;flex-shrink:0}}
.step-info{{flex:1;min-width:0}}.step-title{{font-size:14px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.step-subtitle{{font-size:12px;color:#64748b;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.step-meta{{display:flex;flex-direction:column;align-items:flex-end;gap:4px;flex-shrink:0}}
.step-time{{font-size:11px;color:#64748b;font-family:'JetBrains Mono',monospace}}.step-status{{font-size:16px}}
/* Metrics */
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:20px}}
.metric-card{{background:#161822;border:1px solid rgba(255,255,255,0.04);border-radius:10px;padding:16px}}
.metric-label{{font-size:11px;text-transform:uppercase;color:#64748b;margin-bottom:6px;letter-spacing:0.5px}}
.metric-value{{font-size:24px;font-weight:700;color:#f1f5f9}}
.progress-bar{{background:#1e2030;border-radius:6px;height:8px;overflow:hidden;margin-bottom:6px}}
.progress-fill{{background:linear-gradient(90deg,#4f7df8,#a78bfa);height:100%;border-radius:6px;transition:width 0.5s}}
/* Logs */
.log-panel{{background:#0e1018;border-radius:8px;padding:12px;height:280px;overflow-y:auto;font-family:'JetBrains Mono',monospace;font-size:12px;line-height:1.8}}
.log-entry{{padding:2px 0;border-bottom:1px solid rgba(255,255,255,0.02)}}
.log-ts{{color:#64748b}}.log-step{{color:#4f7df8}}.log-info{{color:#94a3b8}}.log-warn{{color:#fbbf24}}.log-error{{color:#f87171}}.log-retry{{color:#fb923c}}.log-success{{color:#34d399}}
.files-panel{{max-height:200px;overflow-y:auto}}
.file-entry{{font-size:12px;padding:4px 0;font-family:'JetBrains Mono',monospace;color:#94a3b8}}
.file-new{{color:#34d399}}.file-edit{{color:#4f7df8}}.file-delete{{color:#f87171}}
/* Intervention */
.intervention{{background:rgba(248,113,113,0.1);border:1px solid rgba(248,113,113,0.3);border-radius:10px;padding:16px;margin-bottom:16px;display:none}}
.intervention h3{{color:#f87171;margin-bottom:8px;font-size:14px}}
.intervention p{{color:#e2e8f0;font-size:13px;margin-bottom:12px}}
.intervention input{{width:100%;padding:8px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.1);background:#161822;color:#e2e8f0;font-size:13px}}
.intervention button{{margin-top:8px;padding:6px 16px;border-radius:6px;border:none;background:#4f7df8;color:white;cursor:pointer;font-size:13px}}
/* Configure form */
.config-form{{max-width:700px;margin:0 auto}}
.form-section{{margin-bottom:24px}}
.form-section-title{{font-size:13px;font-weight:600;color:#4f7df8;text-transform:uppercase;letter-spacing:1px;margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid rgba(79,125,248,0.2)}}
.form-field{{margin-bottom:16px}}
.form-field label{{display:block;font-size:13px;font-weight:500;margin-bottom:4px;color:#e2e8f0}}
.form-input{{width:100%;padding:10px 14px;border-radius:8px;border:1px solid rgba(255,255,255,0.1);background:#161822;color:#e2e8f0;font-size:14px;font-family:'Inter',sans-serif;transition:border-color .2s}}
.form-input:focus{{outline:none;border-color:#4f7df8}}
textarea.form-input{{resize:vertical;font-family:'JetBrains Mono',monospace;font-size:13px}}
select.form-input{{cursor:pointer}}
.form-hint{{font-size:11px;color:#64748b;margin-top:4px}}
.checkbox-label{{display:flex;align-items:center;gap:8px;font-size:13px;cursor:pointer}}
.checkbox-label input{{width:16px;height:16px;cursor:pointer}}
.secret-badge{{font-size:11px;color:#fbbf24}}
.save-bar{{display:flex;gap:12px;justify-content:center;margin-top:24px;padding-top:20px;border-top:1px solid rgba(255,255,255,0.06)}}
.btn{{padding:10px 28px;border-radius:8px;border:none;font-size:14px;font-weight:600;cursor:pointer;transition:all .2s}}
.btn-primary{{background:#4f7df8;color:white}}.btn-primary:hover{{background:#3b6de0}}
.btn-secondary{{background:#1e2030;color:#94a3b8;border:1px solid rgba(255,255,255,0.1)}}.btn-secondary:hover{{color:#e2e8f0}}
.toast{{position:fixed;top:20px;right:20px;padding:12px 20px;border-radius:8px;font-size:13px;font-weight:500;z-index:1000;transition:opacity .3s;opacity:0}}
.toast.show{{opacity:1}}.toast-ok{{background:#065f46;color:#34d399;border:1px solid #34d399}}.toast-err{{background:#7f1d1d;color:#f87171;border:1px solid #f87171}}
.launch-panel{{display:none;margin-top:24px;background:#0c1018;border:1px solid rgba(52,211,153,0.2);border-radius:12px;padding:20px}}
.launch-panel.show{{display:block}}
.launch-header{{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}}
.launch-header h3{{font-size:14px;color:#34d399;font-weight:600}}
.launch-cmd{{background:#060a0f;border-radius:8px;padding:16px;font-family:'JetBrains Mono',monospace;font-size:13px;color:#34d399;line-height:1.8;word-break:break-all;user-select:all}}
.launch-steps{{margin-top:12px;font-size:12px;color:#64748b;line-height:1.8}}
.launch-steps strong{{color:#fbbf24}}
.btn-green{{background:#065f46;color:#34d399;border:1px solid #34d399}}.btn-green:hover{{background:#064e3b}}
.btn-copy{{padding:6px 14px;border-radius:6px;border:1px solid rgba(52,211,153,0.3);background:rgba(52,211,153,0.1);color:#34d399;font-size:12px;font-weight:500;cursor:pointer}}.btn-copy:hover{{background:rgba(52,211,153,0.2)}}
.btn-secondary{{padding:8px 20px;border-radius:6px;border:1px solid rgba(255,255,255,0.1);background:#1e2030;color:#94a3b8;cursor:pointer;font-size:13px}}.btn-secondary:hover{{color:#e2e8f0}}
@media(max-width:900px){{.layout{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<h1>{project['name']}</h1>
<div class="subtitle">Multi-Agent System Dashboard</div>

<div class="tabs">
  <div class="tab active" onclick="switchTab('configure')">⚙ Configure</div>
  <div class="tab" onclick="switchTab('dashboard')">📊 Dashboard</div>
  <div class="tab" onclick="switchTab('report')">📄 Report</div>
</div>

<div id="toast" class="toast"></div>

<!-- ═══ CONFIGURE TAB ═══ -->
<div id="page-configure" class="tab-page active">
  <div class="config-form">
    {form_fields_html}
    <div class="save-bar">
      <button class="btn btn-green" onclick="saveAndLaunch()">🚀 Save Config & Generate Launch Command</button>
      <button class="btn btn-secondary" onclick="loadConfig()">↻ Reload from File</button>
    </div>

    <div class="launch-panel" id="launch-panel">
      <div class="launch-header">
        <h3>🖥️ Launch Command</h3>
        <button class="btn-copy" onclick="copyLaunchCmd()">📋 Copy</button>
      </div>
      <div class="launch-cmd" id="launch-cmd"></div>
      <div class="launch-steps">
        <strong>Steps:</strong><br>
        1. Copy the command above<br>
        2. Open a terminal in the project directory<br>
        3. Paste and run — the agent will execute autonomously<br>
        4. Switch to the <strong>Dashboard</strong> tab to monitor progress in real-time
      </div>
    </div>
  </div>
</div>

<!-- ═══ DASHBOARD TAB ═══ -->
<div id="page-dashboard" class="tab-page">
  <div class="overall-badge"><span class="badge badge-idle" id="overall-badge">IDLE</span></div>

  <div id="intervention" class="intervention">
    <h3 id="int-title">Human Intervention Needed</h3>
    <p id="int-message"></p>
    <input type="text" id="int-input" placeholder="Type your response...">
    <button onclick="sendResponse()">Send Response</button>
  </div>

  <div class="layout">
    <div>
      <div class="panel">
        <div class="panel-title">Pipeline Steps</div>
        {steps_html}
      </div>
      <div class="panel" style="margin-top:16px">
        <div class="panel-title">Files Changed</div>
        <div class="files-panel" id="files-list"><div class="file-entry">No files changed yet</div></div>
      </div>
    </div>
    <div>
      <div class="grid">{metrics_html_cards}</div>
      <div class="panel">
        <div class="panel-title">Activity Log</div>
        <div class="log-panel" id="log-stream"></div>
      </div>
    </div>
  </div>
</div>

<!-- ═══ REPORT TAB ═══ -->
<div id="page-report" class="tab-page">
  <div style="max-width:1200px;margin:0 auto">
    <div id="report-empty" style="text-align:center;padding:60px 20px">
      <div style="font-size:48px;margin-bottom:16px;opacity:0.3">📄</div>
      <div style="color:#64748b;font-size:15px;margin-bottom:20px">No report generated yet. Run the agent first — the report will appear here automatically.</div>
      <button class="btn btn-secondary" onclick="loadReport()" style="padding:8px 20px;border-radius:6px;border:1px solid rgba(255,255,255,0.1);background:#1e2030;color:#94a3b8;cursor:pointer;font-size:13px">↻ Check for Report</button>
    </div>
    <div id="report-actions" style="display:none;margin-bottom:16px;display:none;justify-content:flex-end;gap:8px">
      <button class="btn-copy" onclick="downloadReport('html')">📥 Download HTML</button>
      <button class="btn-copy" onclick="downloadReport('raw')">📥 Download Raw</button>
      <button class="btn-copy" onclick="loadReport()">↻ Refresh</button>
    </div>
    <div id="report-frame-container" style="display:none">
      <iframe id="report-frame" style="width:100%;border:1px solid rgba(255,255,255,0.06);border-radius:12px;background:white;min-height:600px" sandbox="allow-same-origin"></iframe>
    </div>
    <div id="report-text" style="display:none;background:#12131a;border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:24px;font-family:'JetBrains Mono',monospace;font-size:13px;line-height:1.8;white-space:pre-wrap;max-height:80vh;overflow-y:auto"></div>
  </div>
</div>

<script>
const SECRET_FIELDS = {secret_ids_json};
const STATUS_ICONS = {{pending:'○',running:'◉',done:'✓',failed:'✕',warning:'⚠'}};
const STATUS_COLORS = {{pending:'#64748b',running:'#fbbf24',done:'#34d399',failed:'#f87171',warning:'#fb923c'}};

function switchTab(tab) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-page').forEach(p => p.classList.remove('active'));
  document.getElementById('page-' + tab).classList.add('active');
  document.querySelector(`.tab[onclick*="${{tab}}"]`).classList.add('active');
}}

function showToast(msg, ok) {{
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show ' + (ok ? 'toast-ok' : 'toast-err');
  setTimeout(() => t.className = 'toast', 3000);
}}

async function saveAndLaunch() {{
  const config = {{}};
  const secrets = {{}};
  document.querySelectorAll('.cfg-input').forEach(el => {{
    const fid = el.dataset.field;
    let val;
    if (el.type === 'checkbox') val = el.checked;
    else val = el.value;
    if (SECRET_FIELDS.includes(fid)) secrets[fid] = val;
    else config[fid] = val;
  }});
  try {{
    const r = await fetch('/save-config', {{
      method: 'POST',
      headers: {{'Content-Type':'application/json'}},
      body: JSON.stringify({{ config, secrets }})
    }});
    if (!r.ok) {{ showToast('Failed to save: ' + (await r.text()), false); return; }}
    showToast('Configuration saved! Launch command generated.', true);

    // Get project directory and generate platform-specific launch command
    let projectDir = '.';
    try {{ const dr = await fetch('/project-dir'); if (dr.ok) projectDir = (await dr.json()).path || '.'; }} catch(e) {{}}
    const platform = '{target_platform}';
    let cmd;
    if (platform === 'cursor') {{
      cmd = `Open the project folder in Cursor:\\n  ${{projectDir}}\\n\\nCursor will automatically read .cursorrules and execute the skills.`;
    }} else if (platform === 'codex') {{
      cmd = `cd ${{projectDir}} && codex "Read AGENTS.md and execute all skills in order"`;
    }} else if (platform === 'generic') {{
      cmd = `Open INSTRUCTIONS.md in your AI tool and ask it to:\\n  "Read INSTRUCTIONS.md and config.json, then execute all skills in order"\\n\\nProject path: ${{projectDir}}`;
    }} else {{
      cmd = `cd ${{projectDir}} && claude --dangerously-skip-permissions "Read CLAUDE.md and understand the project and execute all the agents in order"`;
    }}
    document.getElementById('launch-cmd').textContent = cmd;
    document.getElementById('launch-panel').classList.add('show');
  }} catch(e) {{ showToast('Error: ' + e.message, false); }}
}}

function copyLaunchCmd() {{
  const cmd = document.getElementById('launch-cmd').textContent;
  navigator.clipboard.writeText(cmd).then(() => showToast('Command copied to clipboard!', true));
}}

async function loadConfig() {{
  try {{
    const r = await fetch('/config');
    if (!r.ok) return;
    const data = await r.json();
    document.querySelectorAll('.cfg-input').forEach(el => {{
      const fid = el.dataset.field;
      if (fid in data) {{
        if (el.type === 'checkbox') el.checked = !!data[fid];
        else if (!SECRET_FIELDS.includes(fid)) el.value = data[fid] || '';
      }}
    }});
    showToast('Configuration loaded', true);
  }} catch(e) {{}}
}}

async function poll() {{
  try {{
    let r = await fetch('/state');
    if (!r.ok) {{ r = await fetch('logs/dashboard_state.json'); }}
    if (r.ok) update(await r.json());
  }} catch(e) {{}}
}}

function update(d) {{
  const badge = document.getElementById('overall-badge');
  if (d.overall_status) {{
    badge.textContent = d.overall_status.toUpperCase();
    badge.className = 'badge badge-' + d.overall_status;
  }}
  if (d.steps) {{
    for (const [n,s] of Object.entries(d.steps)) {{
      const card=document.getElementById('step-card-'+n), sub=document.getElementById('step-sub-'+n),
            time=document.getElementById('step-time-'+n), status=document.getElementById('step-status-'+n);
      if (!card) continue;
      card.className='step-card'+(s.status==='running'?' active':s.status==='done'?' done':s.status==='failed'?' failed':'');
      if(sub) sub.textContent=s.subtitle||'';
      if(time) time.textContent=s.time||'--';
      if(status) {{status.textContent=STATUS_ICONS[s.status]||'○';status.style.color=STATUS_COLORS[s.status]||'#64748b';}}
    }}
  }}
  for (const [k,v] of Object.entries(d)) {{
    if (['steps','logs','files_changed','human_intervention','overall_status','started_at','finished_at','total_retries'].includes(k)) continue;
    const el=document.getElementById(k); if(!el) continue;
    if (el.classList.contains('progress-fill')) {{ el.style.width=v+'%'; const ve=document.getElementById(k+'_val'); if(ve) ve.textContent=v+'%'; }}
    else {{ el.textContent=v; }}
  }}
  if (d.logs && d.logs.length) {{
    const logEl=document.getElementById('log-stream');
    logEl.innerHTML=d.logs.slice(-50).map(l=>
      `<div class="log-entry"><span class="log-ts">${{l.ts}}</span> <span class="log-step">${{l.step}}</span> <span class="log-${{(l.level||'info').toLowerCase()}}">[$${{l.level}}]</span> ${{l.message}}</div>`
    ).join('');
    logEl.scrollTop=logEl.scrollHeight;
  }}
  if (d.files_changed && d.files_changed.length) {{
    document.getElementById('files-list').innerHTML=d.files_changed.map(f=>
      `<div class="file-entry file-${{f.type||'edit'}}">[${{f.type||'edit'}}] ${{f.path}} <span style="color:#64748b">${{f.time||''}}</span></div>`
    ).join('');
  }}
  const intEl=document.getElementById('intervention');
  if(d.human_intervention&&d.human_intervention.needed){{intEl.style.display='block';document.getElementById('int-title').textContent=d.human_intervention.title||'';document.getElementById('int-message').textContent=d.human_intervention.message||'';}}
  else{{intEl.style.display='none';}}
  // Auto-load report when pipeline succeeds
  if(d.overall_status==='success') loadReport();
}}

async function sendResponse() {{
  const input=document.getElementById('int-input');
  try{{ await fetch('/respond',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{response:input.value}})}}); input.value=''; }}catch(e){{}}
}}

// Load config on page load, start polling
// ── Report Tab ──
async function loadReport() {{
  try {{
    // Try HTML report first
    let r = await fetch('/report');
    if (r.ok) {{
      const contentType = r.headers.get('content-type') || '';
      const text = await r.text();
      if (contentType.includes('html') || text.trim().startsWith('<')) {{
        document.getElementById('report-empty').style.display = 'none';
        document.getElementById('report-frame-container').style.display = 'block';
        document.getElementById('report-text').style.display = 'none';
        document.getElementById('report-actions').style.display = 'flex';
        const frame = document.getElementById('report-frame');
        frame.srcdoc = text;
        // Auto-resize iframe
        frame.onload = () => {{ try {{ frame.style.height = (frame.contentDocument.body.scrollHeight + 40) + 'px'; }} catch(e) {{}} }};
      }} else {{
        // Text/JSON/Markdown report
        document.getElementById('report-empty').style.display = 'none';
        document.getElementById('report-frame-container').style.display = 'none';
        document.getElementById('report-text').style.display = 'block';
        document.getElementById('report-actions').style.display = 'flex';
        document.getElementById('report-text').textContent = text;
      }}
      return;
    }}
  }} catch(e) {{}}
  // No report yet
  document.getElementById('report-empty').style.display = 'block';
  document.getElementById('report-frame-container').style.display = 'none';
  document.getElementById('report-text').style.display = 'none';
  document.getElementById('report-actions').style.display = 'none';
}}

function downloadReport(format) {{
  window.open('/report?download=1', '_blank');
}}

// Auto-check for report when switching to report tab
const origSwitchTab = switchTab;
switchTab = function(tab) {{
  origSwitchTab(tab);
  if (tab === 'report') loadReport();
}};

// Also check for report when pipeline completes
function checkReportOnSuccess(d) {{
  if (d.overall_status === 'success') loadReport();
}}

loadConfig();
setInterval(poll, 2000); poll();
</script>
</body>
</html>"""
    (project_dir / "dashboard.html").write_text(dashboard_html)
    generated_files.append("dashboard.html")

    # Generate enhanced run_server.py with human intervention support
    run_server = f"""#!/usr/bin/env python3
\"\"\"Dashboard server for {project['name']}.
Serves the dashboard UI and handles state I/O + human intervention.\"\"\"
import json
from pathlib import Path
from flask import Flask, send_file, jsonify, request

BASE = Path(__file__).resolve().parent
app = Flask(__name__)

@app.route("/")
def index():
    return send_file(str(BASE / "dashboard.html"))

@app.route("/state")
def state():
    state_file = BASE / "logs" / "dashboard_state.json"
    if state_file.exists():
        try:
            return jsonify(json.loads(state_file.read_text()))
        except json.JSONDecodeError:
            return jsonify({{}})
    return jsonify({{}})

@app.route("/respond", methods=["POST"])
def respond():
    \"\"\"Handle human intervention responses from the dashboard UI.\"\"\"
    data = request.json or {{}}
    response_file = BASE / "logs" / "human_response.json"
    response_file.write_text(json.dumps({{
        "response": data.get("response", ""),
        "timestamp": __import__("datetime").datetime.now().isoformat()
    }}, indent=2))
    # Also clear the intervention flag in dashboard state
    state_file = BASE / "logs" / "dashboard_state.json"
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
            state["human_intervention"] = {{"needed": False}}
            state_file.write_text(json.dumps(state, indent=2))
        except Exception:
            pass
    return jsonify({{"status": "ok"}})

@app.route("/config")
def get_config():
    \"\"\"Return current config.json for the configure form.\"\"\"
    config_file = BASE / "config.json"
    if config_file.exists():
        try:
            return jsonify(json.loads(config_file.read_text()))
        except json.JSONDecodeError:
            return jsonify({{}})
    return jsonify({{}})

@app.route("/save-config", methods=["POST"])
def save_config():
    \"\"\"Save configuration from the dashboard form to config.json and .env.\"\"\"
    data = request.json or {{}}
    config = data.get("config", {{}})
    secrets = data.get("secrets", {{}})

    # Write non-secret values to config.json
    config_file = BASE / "config.json"
    existing = {{}}
    if config_file.exists():
        try:
            existing = json.loads(config_file.read_text())
        except json.JSONDecodeError:
            pass
    # Merge: keep secret refs, update non-secret values
    for k, v in config.items():
        existing[k] = v
    config_file.write_text(json.dumps(existing, indent=2))

    # Write secrets to .env
    if secrets:
        env_file = BASE / ".env"
        env_lines = []
        if env_file.exists():
            env_lines = env_file.read_text().splitlines()
        # Update or add each secret
        for key, val in secrets.items():
            env_key = key.upper()
            updated = False
            for i, line in enumerate(env_lines):
                if line.startswith(env_key + "="):
                    env_lines[i] = f"{{env_key}}={{val}}"
                    updated = True
                    break
            if not updated:
                env_lines.append(f"{{env_key}}={{val}}")
        env_file.write_text("\\n".join(env_lines) + "\\n")

    return jsonify({{"status": "ok"}})

@app.route("/project-dir")
def project_dir():
    \"\"\"Return the absolute path of this project directory.\"\"\"
    return jsonify({{"path": str(BASE)}})

@app.route("/report")
def report():
    \"\"\"Serve the generated report file. Looks for report.html, then report.md, report.json, report.txt in project root and reports/ dir.\"\"\"
    search_names = ["report.html", "report.md", "report.json", "report.txt"]
    search_dirs = [BASE, BASE / "reports", BASE / "output", BASE / "logs"]
    for d in search_dirs:
        for name in search_names:
            f = d / name
            if f.exists() and f.stat().st_size > 0:
                content_types = {{".html": "text/html", ".md": "text/plain", ".json": "application/json", ".txt": "text/plain"}}
                ctype = content_types.get(f.suffix, "text/plain")
                if request.args.get("download"):
                    return send_file(str(f), as_attachment=True, download_name=name)
                return f.read_text(), 200, {{"Content-Type": ctype}}
    # Also check for any HTML file in reports/ or output/
    for d in [BASE / "reports", BASE / "output"]:
        if d.exists():
            for f in sorted(d.glob("*.html")):
                if f.stat().st_size > 0:
                    if request.args.get("download"):
                        return send_file(str(f), as_attachment=True)
                    return f.read_text(), 200, {{"Content-Type": "text/html"}}
    return "No report generated yet.", 404

@app.route("/audit")
def audit():
    \"\"\"Return the audit log.\"\"\"
    audit_file = BASE / "logs" / "audit.log"
    if audit_file.exists():
        return audit_file.read_text(), 200, {{"Content-Type": "text/plain"}}
    return "No audit log yet.", 200, {{"Content-Type": "text/plain"}}

if __name__ == "__main__":
    print("Dashboard running at http://localhost:8080")
    app.run(host="0.0.0.0", port=8080, debug=True)
"""
    (project_dir / "run_server.py").write_text(run_server)
    generated_files.append("run_server.py")

    # Generate .gitignore
    (project_dir / ".gitignore").write_text(".env\nlogs/\n__pycache__/\nvenv/\n*.pyc\n")
    generated_files.append(".gitignore")

    # Generate empty audit log
    (logs_dir / "audit.log").write_text("")
    generated_files.append("logs/audit.log")

    # Generate empty human_response.json
    (logs_dir / "human_response.json").write_text("{}")
    generated_files.append("logs/human_response.json")

    project["generated_files"] = generated_files
    project["status"] = "generated"
    project["project_dir"] = str(project_dir)

    return generated_files, str(project_dir)