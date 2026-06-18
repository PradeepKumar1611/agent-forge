"""Authentication, user management, and activity logging for Agent Factory.

Uses a simple JSON file for users and JSONL file for activity logs.
No database required.
"""
import hashlib
import json
import os
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import jsonify, redirect, request, session, url_for

BASE_DIR = Path(__file__).resolve().parent
USERS_FILE = BASE_DIR / "users.json"
ACTIVITY_LOG = BASE_DIR / "logs" / "activity.jsonl"


def _hash_password(password):
    """Hash password with SHA-256 + salt."""
    salt = "agent-factory-salt"  # Simple fixed salt — adequate for file-based auth
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()


def _load_users():
    """Load users from JSON file."""
    if USERS_FILE.exists():
        try:
            return json.loads(USERS_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save_users(users):
    """Save users to JSON file."""
    USERS_FILE.write_text(json.dumps(users, indent=2))


def init_users():
    """Create default admin user on first startup if no users exist."""
    if USERS_FILE.exists() and _load_users():
        return

    admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")
    users = {
        "admin": {
            "password_hash": _hash_password(admin_password),
            "name": "Administrator",
            "role": "admin",
            "created": datetime.now().isoformat(),
        }
    }
    _save_users(users)
    print(f"  Default admin user created (username: admin, password: {'from .env' if os.environ.get('ADMIN_PASSWORD') else 'admin123'})")


def authenticate(username, password):
    """Verify username/password. Returns user dict or None."""
    users = _load_users()
    user = users.get(username)
    if user and user["password_hash"] == _hash_password(password):
        return user
    return None


def get_current_user():
    """Get current logged-in user from session. Returns (username, user_dict) or (None, None)."""
    username = session.get("username")
    if not username:
        return None, None
    users = _load_users()
    user = users.get(username)
    if not user:
        return None, None
    return username, user


def login_required(f):
    """Decorator to require login for a route."""
    @wraps(f)
    def decorated(*args, **kwargs):
        username, user = get_current_user()
        if not username:
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "Login required"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Decorator to require admin role."""
    @wraps(f)
    def decorated(*args, **kwargs):
        username, user = get_current_user()
        if not username:
            if request.is_json:
                return jsonify({"error": "Login required"}), 401
            return redirect(url_for("login_page"))
        if user.get("role") != "admin":
            if request.is_json:
                return jsonify({"error": "Admin access required"}), 403
            return "Access denied", 403
        return f(*args, **kwargs)
    return decorated


def add_user(username, password, name, role="user"):
    """Add a new user. Returns (success, message)."""
    users = _load_users()
    if username in users:
        return False, f"User '{username}' already exists"
    if len(username) < 3:
        return False, "Username must be at least 3 characters"
    if len(password) < 4:
        return False, "Password must be at least 4 characters"
    users[username] = {
        "password_hash": _hash_password(password),
        "name": name or username,
        "role": role,
        "created": datetime.now().isoformat(),
    }
    _save_users(users)
    return True, f"User '{username}' created"


def delete_user(username):
    """Delete a user. Returns (success, message)."""
    users = _load_users()
    if username not in users:
        return False, f"User '{username}' not found"
    if username == "admin":
        return False, "Cannot delete the admin user"
    del users[username]
    _save_users(users)
    return True, f"User '{username}' deleted"


def change_password(username, old_password, new_password):
    """Change user password. Returns (success, message)."""
    users = _load_users()
    user = users.get(username)
    if not user:
        return False, "User not found"
    if user["password_hash"] != _hash_password(old_password):
        return False, "Current password is incorrect"
    if len(new_password) < 4:
        return False, "New password must be at least 4 characters"
    user["password_hash"] = _hash_password(new_password)
    _save_users(users)
    return True, "Password changed successfully"


def list_users():
    """List all users (without password hashes)."""
    users = _load_users()
    return [
        {"username": k, "name": v.get("name", k), "role": v.get("role", "user"), "created": v.get("created", "")}
        for k, v in users.items()
    ]


def log_activity(username, action, project_id=None, query=None):
    """Append an activity entry to the JSONL log."""
    ACTIVITY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now().isoformat(),
        "user": username,
        "action": action,
        "project_id": project_id,
    }
    if query:
        entry["query"] = query[:500]  # Truncate to prevent huge logs
    with open(ACTIVITY_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
