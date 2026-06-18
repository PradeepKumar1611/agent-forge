"""Agent Factory — Main Flask application.

Routes for project CRUD, design (describe/chat), generation, and download.
Security, Claude CLI, and file generation logic live in separate modules.
"""
import json
import os
import re
import shutil
import uuid
import zipfile
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for

from auth import (
    add_user,
    admin_required,
    authenticate,
    change_password,
    delete_user,
    get_current_user,
    init_users,
    list_users,
    log_activity,
    login_required,
)
from claude_client import run_claude_code, extract_json_from_text
from database import init_db, create_project as db_create, get_project as _db_get_raw, save_project as db_save, list_projects_for_user as _db_list_raw, delete_project as db_delete
from generator import generate_project_files
from security import (
    SECURITY_PROMPT_SUFFIX,
    register_middleware,
    sanitize_input,
)

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
PROJECTS_DIR = BASE_DIR / "generated_projects"
PROJECTS_DIR.mkdir(exist_ok=True)

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "templates"),
    static_url_path="/static",
)
# Persist session key so users stay logged in across restarts
_secret_key = os.environ.get("FLASK_SECRET_KEY")
if not _secret_key:
    _env_path = BASE_DIR / ".env"
    _secret_key = os.urandom(24).hex()
    # Append to .env so it persists — only if not already present (idempotent)
    _existing_env = _env_path.read_text() if _env_path.exists() else ""
    if "FLASK_SECRET_KEY=" not in _existing_env:
        with open(_env_path, "a") as f:
            f.write(f"\nFLASK_SECRET_KEY={_secret_key}\n")
app.config["SECRET_KEY"] = _secret_key

# Session cookie hardening. SESSION_COOKIE_SECURE is opt-in via env so local
# (HTTP) development still works; set it to "true" behind HTTPS in production.
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "").lower() == "true",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=24),
)

# Initialize database and users
init_db()
init_users()

# Register security middleware
register_middleware(app, jsonify, request)

# Supported platforms
PLATFORMS = {
    "claude_code": {"name": "Claude Code", "entry_file": "CLAUDE.md"},
    "cursor": {"name": "Cursor", "entry_file": ".cursorrules"},
    "codex": {"name": "Codex / OpenAI", "entry_file": "AGENTS.md"},
    "generic": {"name": "Generic (Any AI)", "entry_file": "INSTRUCTIONS.md"},
}


def normalize_project(project):
    """Ensure project uses 'skills' key, migrating from 'sub_agents' if needed."""
    if not project:
        return project
    if "skills" not in project and "sub_agents" in project:
        project["skills"] = project["sub_agents"]
    elif "skills" not in project:
        project["skills"] = []
    # Keep sub_agents as alias for backward compat
    project["sub_agents"] = project["skills"]
    if "target_platform" not in project:
        project["target_platform"] = "claude_code"
    return project


def db_get(project_id):
    return normalize_project(_db_get_raw(project_id))


def db_list(username, role):
    return [normalize_project(p) for p in _db_list_raw(username, role)]


def project_access(project_id, *, write):
    """Fetch a project and enforce access control.

    Returns (project, None) when the current user may proceed, otherwise
    (None, (response, status)) which the caller should return directly.

    - write=True  → only the owner or an admin may access (mutating routes).
    - write=False → owner/admin OR any public project (read-only routes).
    """
    project = db_get(project_id)
    if not project:
        return None, (jsonify({"error": "Project not found"}), 404)
    username = session.get("username")
    role = session.get("role")
    is_owner = project.get("created_by") == username or role == "admin"
    if is_owner or (not write and project.get("is_public")):
        return project, None
    return None, (jsonify({"error": "You don't have access to this project"}), 403)


# ═══════════════════════════════════════════════════════════════
# AUTH ROUTES
# ═══════════════════════════════════════════════════════════════

@app.route("/login", methods=["GET"])
def login_page():
    if session.get("username"):
        return redirect("/")
    return render_template("login.html")


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    data = request.json or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    user = authenticate(username, password)
    if not user:
        return jsonify({"error": "Invalid username or password"}), 401
    session["username"] = username
    session["role"] = user.get("role", "user")
    session["name"] = user.get("name", username)
    log_activity(username, "login")
    return jsonify({"message": "Login successful", "username": username, "name": user.get("name", username), "role": user.get("role", "user")})


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    username = session.get("username", "unknown")
    log_activity(username, "logout")
    session.clear()
    return jsonify({"message": "Logged out"})


@app.route("/api/auth/me", methods=["GET"])
def api_me():
    username, user = get_current_user()
    if not username:
        return jsonify({"error": "Not logged in"}), 401
    return jsonify({"username": username, "name": user.get("name", username), "role": user.get("role", "user")})


@app.route("/api/auth/change-password", methods=["POST"])
@login_required
def api_change_password():
    data = request.json or {}
    username = session.get("username")
    ok, msg = change_password(username, data.get("old_password", ""), data.get("new_password", ""))
    if not ok:
        return jsonify({"error": msg}), 400
    log_activity(username, "change_password")
    return jsonify({"message": msg})


# ═══════════════════════════════════════════════════════════════
# ADMIN ROUTES
# ═══════════════════════════════════════════════════════════════

@app.route("/admin")
@admin_required
def admin_page():
    return render_template("admin.html")


@app.route("/api/admin/users", methods=["GET"])
@admin_required
def api_list_users():
    return jsonify(list_users())


@app.route("/api/admin/users", methods=["POST"])
@admin_required
def api_add_user():
    data = request.json or {}
    ok, msg = add_user(
        data.get("username", "").strip(),
        data.get("password", ""),
        data.get("name", "").strip(),
        data.get("role", "user"),
    )
    if not ok:
        return jsonify({"error": msg}), 400
    log_activity(session.get("username"), "add_user", query=data.get("username"))
    return jsonify({"message": msg})


@app.route("/api/admin/users/<username>", methods=["DELETE"])
@admin_required
def api_delete_user(username):
    ok, msg = delete_user(username)
    if not ok:
        return jsonify({"error": msg}), 400
    log_activity(session.get("username"), "delete_user", query=username)
    return jsonify({"message": msg})


@app.route("/api/admin/activity", methods=["GET"])
@admin_required
def api_activity():
    log_file = BASE_DIR / "logs" / "activity.jsonl"
    if not log_file.exists():
        return jsonify([])
    lines = []
    for line in log_file.read_text().strip().splitlines()[-100:]:
        try:
            lines.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return jsonify(lines)


# ═══════════════════════════════════════════════════════════════
# APP ROUTES (protected)
# ═══════════════════════════════════════════════════════════════

@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/api/projects", methods=["POST"])
@login_required
def create_project():
    data = request.json or {}
    project_id = str(uuid.uuid4())[:8]
    username = session.get("username", "unknown")
    project = {
        "id": project_id,
        "name": data.get("name", "Untitled Agent"),
        "description": "",
        "created_at": datetime.now().isoformat(),
        "created_by": username,
        "status": "new",
        "tags": data.get("tags", []),
        "is_public": False,
        "target_platform": data.get("target_platform", "claude_code"),
        "form_fields": [],
        "skills": [],
        "sub_agents": [],  # backward compat alias
        "tools_needed": [],
        "mcp_servers": [],
        "dashboard_metrics": [],
        "form_values": {},
        "chat_history": [],
        "generated_files": [],
    }
    db_create(project)
    log_activity(username, "create_project", project_id)
    return jsonify(project), 201


# ── Platforms ──

@app.route("/api/platforms", methods=["GET"])
@login_required
def get_platforms():
    return jsonify(PLATFORMS)


# ── Tags ──

@app.route("/api/projects/<project_id>/tags", methods=["POST"])
@login_required
def update_tags(project_id):
    project, err = project_access(project_id, write=True)
    if err:
        return err
    data = request.json or {}
    tags = data.get("tags", [])
    # Sanitize: lowercase, strip, deduplicate, max 10 tags
    project["tags"] = list(set(t.strip().lower() for t in tags if t.strip()))[:10]
    db_save(project)
    return jsonify({"tags": project["tags"]})


# ── Visibility ──

@app.route("/api/projects/<project_id>/visibility", methods=["POST"])
@login_required
def toggle_visibility(project_id):
    project, err = project_access(project_id, write=True)
    if err:
        return err
    data = request.json or {}
    project["is_public"] = bool(data.get("is_public", False))
    db_save(project)
    return jsonify({"is_public": project["is_public"]})


# ── Clone ──

@app.route("/api/projects/<project_id>/clone", methods=["POST"])
@login_required
def clone_project(project_id):
    source, err = project_access(project_id, write=False)
    if err:
        return err

    new_id = str(uuid.uuid4())[:8]
    username = session.get("username", "unknown")
    clone = {
        "id": new_id,
        "name": source.get("name", "Untitled") + " (Copy)",
        "description": source.get("description", ""),
        "created_at": datetime.now().isoformat(),
        "created_by": username,
        "status": "form_designed",
        "tags": source.get("tags", []),
        "target_platform": source.get("target_platform", "claude_code"),
        "form_fields": source.get("form_fields", []),
        "skills": source.get("skills", []),
        "sub_agents": source.get("skills", []),
        "tools_needed": source.get("tools_needed", []),
        "mcp_servers": source.get("mcp_servers", []),
        "dashboard_metrics": source.get("dashboard_metrics", []),
        "form_values": {},
        "chat_history": [],
        "generated_files": [],
    }
    db_create(clone)
    log_activity(username, "clone_project", new_id, f"cloned from {project_id}")
    return jsonify(clone), 201


# ── Export / Import ──

@app.route("/api/projects/<project_id>/export", methods=["GET"])
@login_required
def export_project(project_id):
    project, err = project_access(project_id, write=False)
    if err:
        return err
    export_data = {
        "name": project.get("name"),
        "description": project.get("description"),
        "tags": project.get("tags", []),
        "target_platform": project.get("target_platform", "claude_code"),
        "form_fields": project.get("form_fields", []),
        "skills": project.get("skills", []),
        "sub_agents": project.get("skills", []),
        "tools_needed": project.get("tools_needed", []),
        "mcp_servers": project.get("mcp_servers", []),
        "dashboard_metrics": project.get("dashboard_metrics", []),
        "exported_at": datetime.now().isoformat(),
        "exported_by": session.get("username"),
    }
    return jsonify(export_data)


@app.route("/api/projects/import", methods=["POST"])
@login_required
def import_project():
    data = request.json or {}
    if not data.get("form_fields") or not (data.get("skills") or data.get("sub_agents")):
        return jsonify({"error": "Invalid import data — must have form_fields and skills"}), 400

    new_id = str(uuid.uuid4())[:8]
    username = session.get("username", "unknown")
    imported_skills = data.get("skills") or data.get("sub_agents", [])
    project = {
        "id": new_id,
        "name": data.get("name", "Imported Project"),
        "description": data.get("description", ""),
        "created_at": datetime.now().isoformat(),
        "created_by": username,
        "status": "form_designed",
        "tags": data.get("tags", []),
        "target_platform": data.get("target_platform", "claude_code"),
        "form_fields": data.get("form_fields", []),
        "skills": imported_skills,
        "sub_agents": imported_skills,
        "tools_needed": data.get("tools_needed", []),
        "mcp_servers": data.get("mcp_servers", []),
        "dashboard_metrics": data.get("dashboard_metrics", []),
        "form_values": {},
        "chat_history": [],
        "generated_files": [],
    }
    db_create(project)
    log_activity(username, "import_project", new_id, data.get("name", ""))
    return jsonify(project), 201


@app.route("/api/projects", methods=["GET"])
@login_required
def list_projects():
    username = session.get("username")
    role = session.get("role")
    user_projects = [
        {k: v for k, v in p.items() if k != "secret_values"}
        for p in db_list(username, role)
    ]
    return jsonify(user_projects)


@app.route("/api/projects/<project_id>", methods=["GET"])
@login_required
def get_project(project_id):
    project, err = project_access(project_id, write=False)
    if err:
        return err
    safe_project = {k: v for k, v in project.items() if k != "secret_values"}
    return jsonify(safe_project)


@app.route("/api/projects/<project_id>", methods=["DELETE"])
@login_required
def delete_project_route(project_id):
    project, err = project_access(project_id, write=True)
    if err:
        return err

    # Best-effort cleanup of generated files on disk
    project_dir_str = project.get("project_dir")
    if project_dir_str:
        try:
            project_dir = Path(project_dir_str).resolve()
            # Safety: only remove directories under PROJECTS_DIR
            if project_dir.exists() and PROJECTS_DIR.resolve() in project_dir.parents:
                shutil.rmtree(project_dir, ignore_errors=True)
        except (OSError, ValueError):
            pass

    db_delete(project_id)
    log_activity(session.get("username"), "delete_project", project_id, project.get("name"))
    return jsonify({"message": "Project deleted"})


# ── Describe (design agent system from description) ──

@app.route("/api/projects/<project_id>/describe", methods=["POST"])
@login_required
def describe_project(project_id):
    project, err = project_access(project_id, write=True)
    if err:
        return err

    data = request.json or {}
    log_activity(session.get("username"), "describe", project_id, data.get("description", "")[:200])
    description = sanitize_input(data.get("description", ""))
    project["description"] = description
    project["chat_history"].append({"role": "user", "content": description})

    prompt = f"""You are designing a multi-skill agent system. The user wants: {description}

Design this system and respond with ONLY a JSON object (no markdown, no explanation) with these keys:
- "project_name": A short, clean project name (2-4 words, e.g. "Doc Generator", "Code Migration Pipeline") — NOT the user's full description
- "message": A friendly explanation of what you've designed (string)
- "form_fields": Array of input fields the user needs to fill. Each field has: id (string), label (string), type (text|password|textarea|select|checkbox|number|url), placeholder (string), required (boolean), is_secret (boolean), description (string), group (string like "Repository", "Configuration", "Credentials")
- "skills": Array of skills (reusable, platform-agnostic units of work). Each has: name (string), description (string), skill_file (string like "01_skill_name.md"), inputs (array of strings — what this skill reads), outputs (array of strings — what this skill produces)
- "tools_needed": Array of tool names (strings) the skills will use
- "mcp_servers": Array of MCP server names (strings) if needed, otherwise empty array

Important:
- Include password/credential fields with is_secret: true for anything sensitive
- Group related fields together
- The form_fields MUST include all inputs the skills need to know WHAT to work on (target URLs, file paths, repository locations, directories, etc.)
- ALWAYS include a "Report Output" group at the end with these fields:
  - report_format: type=select, options=["HTML","PDF","Markdown","JSON"], label="Report Format", description="Format for the final output report", required=true, default="HTML"
  - report_output_dir: type=text, label="Report Output Directory", placeholder="./reports", description="Directory where the final report will be saved"
  The LAST skill must generate the report in the chosen format and save it to report_output_dir. For HTML reports, also save as "report.html" in the project root so the dashboard can display it.
- Decompose the work into as many independent skills as it genuinely requires — do NOT pad or truncate to hit a fixed number. Simple jobs may need just 2-3 skills; complex, multi-stage pipelines may need 8 or more. Let the scope decide. Each skill should be one cohesive, reusable unit of work (single responsibility).
- Each skill should be platform-agnostic (works with Claude Code, Cursor, Codex, or any AI tool)
- Be specific about tools (git, docker, playwright, npm, etc.)
{SECURITY_PROMPT_SUFFIX}

Respond with ONLY the JSON object."""

    result = run_claude_code(prompt, project_id)
    if result.get("error"):
        return jsonify({"error": f"Design failed: {result.get('text', 'Claude Code error')}"}), 502
    response_text = result.get("text", "")

    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError:
        parsed = extract_json_from_text(response_text)

    if parsed:
        if "project_name" in parsed:
            project["name"] = parsed["project_name"]
        if "form_fields" in parsed:
            project["form_fields"] = parsed["form_fields"]
        # Accept both "skills" and "sub_agents" from Claude response
        skills = parsed.get("skills") or parsed.get("sub_agents") or []
        # Normalize skill_file / instruction_file
        for sk in skills:
            if "skill_file" not in sk and "instruction_file" in sk:
                sk["skill_file"] = sk["instruction_file"]
            elif "instruction_file" not in sk and "skill_file" in sk:
                sk["instruction_file"] = sk["skill_file"]
        project["skills"] = skills
        project["sub_agents"] = skills  # backward compat
        if "tools_needed" in parsed:
            project["tools_needed"] = parsed["tools_needed"]
        if "mcp_servers" in parsed:
            project["mcp_servers"] = parsed.get("mcp_servers", [])
        message = parsed.get("message", "I've designed your system. Here's the input form and skills breakdown.")
    else:
        message = response_text or "I've designed your system based on your description."

    project["status"] = "form_designed"
    project["chat_history"].append({"role": "assistant", "content": message})
    db_save(project)

    return jsonify({
        "message": message,
        "project_name": project["name"],
        "form_fields": project["form_fields"],
        "skills": project["skills"],
        "sub_agents": project["skills"],  # backward compat
        "tools_needed": project["tools_needed"],
        "mcp_servers": project["mcp_servers"],
    })


# ── Chat (refine design) ──

@app.route("/api/projects/<project_id>/chat", methods=["POST"])
@login_required
def chat_with_project(project_id):
    project, err = project_access(project_id, write=True)
    if err:
        return err

    data = request.json or {}
    user_message = sanitize_input(data.get("message", ""))
    project["chat_history"].append({"role": "user", "content": user_message})

    context = f"""You are helping design a multi-skill agent system.
Current project: {project['name']}
Description: {project['description']}
Current form fields: {json.dumps(project['form_fields'])}
Current skills: {json.dumps(project['skills'])}
Current tools: {json.dumps(project['tools_needed'])}
Current dashboard metrics: {json.dumps(project['dashboard_metrics'])}

Chat history:
{chr(10).join(f"{m['role']}: {m['content']}" for m in project['chat_history'][-6:])}

The user says: {user_message}

If the user wants to modify the form, skills, or dashboard, respond with a JSON object containing:
- "message": Your response text
- "form_fields": Updated array (only if changed)
- "skills": Updated array (only if changed). Each skill has: name, description, skill_file, inputs, outputs
- "tools_needed": Updated array (only if changed)
- "dashboard_metrics": Updated array (only if changed)

If no structural changes needed, just respond with a JSON object with "message" only.
{SECURITY_PROMPT_SUFFIX}

Respond with ONLY the JSON object."""

    result = run_claude_code(context, project_id)
    if result.get("error"):
        return jsonify({"error": f"Chat failed: {result.get('text', 'Claude Code error')}"}), 502
    response_text = result.get("text", "")

    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError:
        parsed = extract_json_from_text(response_text)

    if parsed:
        if "form_fields" in parsed:
            project["form_fields"] = parsed["form_fields"]
        skills = parsed.get("skills") or parsed.get("sub_agents")
        if skills:
            project["skills"] = skills
            project["sub_agents"] = skills
        if "tools_needed" in parsed:
            project["tools_needed"] = parsed["tools_needed"]
        if "dashboard_metrics" in parsed:
            project["dashboard_metrics"] = parsed["dashboard_metrics"]
        message = parsed.get("message", response_text)
    else:
        message = response_text

    project["chat_history"].append({"role": "assistant", "content": message})
    db_save(project)

    return jsonify({
        "message": message,
        "form_fields": project["form_fields"],
        "skills": project["skills"],
        "sub_agents": project["skills"],
        "tools_needed": project["tools_needed"],
        "dashboard_metrics": project["dashboard_metrics"],
    })


# ── Form values ──

@app.route("/api/projects/<project_id>/form-values", methods=["POST"])
@login_required
def save_form_values(project_id):
    project, err = project_access(project_id, write=True)
    if err:
        return err

    data = request.json or {}
    values = data.get("values", {})

    secret_fields = {f["id"] for f in project["form_fields"] if f.get("is_secret")}
    safe_values = {}
    secret_values = {}

    for key, val in values.items():
        if key in secret_fields:
            secret_values[key] = val
        else:
            safe_values[key] = val

    project["form_values"] = safe_values
    project["secret_values"] = secret_values
    project["status"] = "form_filled"
    db_save(project)

    return jsonify({"message": "Form values saved.", "secret_count": len(secret_values)})


# ── Dashboard design ──

@app.route("/api/projects/<project_id>/design-dashboard", methods=["POST"])
@login_required
def design_dashboard(project_id):
    project, err = project_access(project_id, write=True)
    if err:
        return err

    prompt = f"""Design dashboard metrics for a multi-skill agent system.
Project: {project['name']}
Description: {project['description']}
Skills: {json.dumps(project['skills'])}

Respond with ONLY a JSON object with:
- "message": Explanation of the dashboard design
- "dashboard_metrics": Array of metrics. Each has: id (string), label (string), type (progress_bar|counter|log_stream|status_badge|percentage|timer|error_count|file_list), description (string), agent (string - which sub-agent updates this)

Include as many meaningful metrics as the system warrants (typically 4-8, but more for complex pipelines with many skills) covering progress, errors, timing, and per-skill status. Add a status metric for each skill where it makes sense — do not artificially cap the count.
Respond with ONLY the JSON object."""

    result = run_claude_code(prompt, project_id)
    if result.get("error"):
        return jsonify({"error": f"Dashboard design failed: {result.get('text', 'Claude Code error')}"}), 502
    response_text = result.get("text", "")

    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError:
        parsed = extract_json_from_text(response_text)

    if parsed and "dashboard_metrics" in parsed:
        project["dashboard_metrics"] = parsed["dashboard_metrics"]
        message = parsed.get("message", "Dashboard metrics designed.")
    else:
        project["dashboard_metrics"] = [
            {"id": "overall_progress", "label": "Overall Progress", "type": "progress_bar", "description": "Total completion percentage", "agent": "all"},
            {"id": "active_agent", "label": "Active Agent", "type": "status_badge", "description": "Currently running agent", "agent": "all"},
            {"id": "errors", "label": "Errors", "type": "error_count", "description": "Total errors encountered", "agent": "all"},
            {"id": "logs", "label": "Activity Log", "type": "log_stream", "description": "Live log output", "agent": "all"},
            {"id": "elapsed", "label": "Elapsed Time", "type": "timer", "description": "Time since start", "agent": "all"},
            {"id": "files_processed", "label": "Files Processed", "type": "counter", "description": "Number of files processed", "agent": "all"},
        ]
        message = response_text or "Dashboard metrics designed with default layout."

    project["status"] = "dashboard_designed"
    project["chat_history"].append({"role": "assistant", "content": message})
    db_save(project)

    return jsonify({
        "message": message,
        "dashboard_metrics": project["dashboard_metrics"],
    })


# ── Generate project files ──

@app.route("/api/projects/<project_id>/generate", methods=["POST"])
@login_required
def generate_project(project_id):
    project, err = project_access(project_id, write=True)
    if err:
        return err

    # Check if platform was passed in request body
    data = request.json or {}
    if data.get("target_platform"):
        project["target_platform"] = data["target_platform"]

    log_activity(session.get("username"), "generate", project_id, project.get("name"))

    generated_files, project_dir = generate_project_files(project, project_id, PROJECTS_DIR)

    project["generated_files"] = generated_files
    project["status"] = "generated"
    project["project_dir"] = project_dir
    db_save(project)

    return jsonify({
        "message": f"Project generated with {len(generated_files)} files!",
        "files": generated_files,
        "project_dir": project_dir,
    })


# ── Download ──

@app.route("/api/projects/<project_id>/download", methods=["GET"])
@login_required
def download_project(project_id):
    project, err = project_access(project_id, write=False)
    if err:
        return err

    project_dir_str = project.get("project_dir")
    if not project_dir_str:
        return jsonify({"error": "Project not generated yet"}), 400

    project_dir = Path(project_dir_str)
    if not project_dir.exists():
        return jsonify({"error": "Project directory not found"}), 404

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in project_dir.rglob("*"):
            if file_path.is_file():
                arcname = file_path.relative_to(project_dir.parent)
                zf.write(file_path, arcname)
    buffer.seek(0)

    safe_name = re.sub(r"[^a-z0-9_]", "_", project["name"].lower().strip()) or "agent"
    return send_file(
        buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{safe_name}_agent.zip",
    )


# ── Project state (for dashboard polling) ──

@app.route("/api/projects/<project_id>/state", methods=["GET"])
@login_required
def get_project_state(project_id):
    project, err = project_access(project_id, write=False)
    if err:
        return err

    project_dir_str = project.get("project_dir")
    if not project_dir_str:
        return jsonify({})

    state_file = Path(project_dir_str) / "logs" / "dashboard_state.json"
    if state_file.exists():
        try:
            return jsonify(json.loads(state_file.read_text()))
        except json.JSONDecodeError:
            return jsonify({})
    return jsonify({})


# ═══════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    # Debug (and the interactive Werkzeug debugger) is OFF unless explicitly opted in.
    debug_mode = os.environ.get("FLASK_DEBUG", "").lower() == "true"
    print(f"Agent Factory running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug_mode, threaded=True)
