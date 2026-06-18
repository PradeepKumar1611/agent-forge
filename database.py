"""SQLite-backed project store for Agent Forge.

Stores all project data in a single SQLite database file.
Each project is stored as a JSON blob — simple, no ORM, no migrations.
Drop-in replacement for the in-memory dict.
"""
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "agent_factory.db"


def _get_conn():
    """Get a SQLite connection with WAL mode for concurrent reads."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create the projects table if it doesn't exist."""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            created_by TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            name TEXT DEFAULT '',
            status TEXT DEFAULT 'new'
        )
    """)
    conn.commit()
    conn.close()


def create_project(project_dict):
    """Insert a new project. project_dict must have 'id' key."""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO projects (id, data, created_by, created_at, name, status) VALUES (?, ?, ?, ?, ?, ?)",
        (
            project_dict["id"],
            json.dumps(project_dict),
            project_dict.get("created_by", ""),
            project_dict.get("created_at", ""),
            project_dict.get("name", ""),
            project_dict.get("status", "new"),
        ),
    )
    conn.commit()
    conn.close()


def get_project(project_id):
    """Get a project by ID. Returns dict or None."""
    conn = _get_conn()
    row = conn.execute("SELECT data FROM projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    if row:
        return json.loads(row[0])
    return None


def save_project(project_dict):
    """Update an existing project (full replace)."""
    conn = _get_conn()
    conn.execute(
        "UPDATE projects SET data = ?, name = ?, status = ? WHERE id = ?",
        (
            json.dumps(project_dict),
            project_dict.get("name", ""),
            project_dict.get("status", ""),
            project_dict["id"],
        ),
    )
    conn.commit()
    conn.close()


def list_projects_for_user(username, role):
    """List projects. Admins see all, users see own + public projects."""
    conn = _get_conn()
    if role == "admin":
        rows = conn.execute("SELECT data FROM projects ORDER BY created_at DESC").fetchall()
    else:
        # User sees their own projects + any public projects from others
        rows = conn.execute(
            "SELECT data FROM projects ORDER BY created_at DESC",
        ).fetchall()
    conn.close()
    all_projects = [json.loads(row[0]) for row in rows]
    if role == "admin":
        return all_projects
    # Filter: own projects + public projects from others
    return [p for p in all_projects if p.get("created_by") == username or p.get("is_public")]


def delete_project(project_id):
    """Delete a project by ID."""
    conn = _get_conn()
    conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()
    conn.close()
