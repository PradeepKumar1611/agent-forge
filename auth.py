"""Authentication, user management, and activity logging for Agent Forge.

Uses a simple JSON file for users and JSONL file for activity logs.
No database required.
"""
import hashlib
import hmac
import json
import os
from datetime import datetime
from functools import wraps
from pathlib import Path

import bcrypt
from flask import jsonify, redirect, request, session, url_for

BASE_DIR = Path(__file__).resolve().parent
USERS_FILE = BASE_DIR / "users.json"
ACTIVITY_LOG = BASE_DIR / "logs" / "activity.jsonl"


def _hash_password(password):
    """Hash a password with bcrypt (per-password random salt)."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _legacy_sha256(password):
    """Legacy hashing scheme (fixed-salt SHA-256). Kept ONLY to verify pre-existing
    accounts so they can be transparently migrated to bcrypt on next login."""
    salt = "agent-factory-salt"
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()


def _verify_password(password, stored_hash):
    """Verify a password against a stored hash.

    Returns (is_valid, needs_rehash). needs_rehash is True when the stored hash
    used the legacy SHA-256 scheme and should be upgraded to bcrypt.
    """
    if not stored_hash:
        return False, False
    if stored_hash.startswith("$2"):  # bcrypt hash
        try:
            return bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8")), False
        except ValueError:
            return False, False
    # Legacy SHA-256 hex digest
    is_valid = hmac.compare_digest(stored_hash, _legacy_sha256(password))
    return is_valid, is_valid


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
    """Verify username/password. Returns user dict or None.

    Transparently upgrades legacy SHA-256 hashes to bcrypt on successful login.
    """
    users = _load_users()
    user = users.get(username)
    if not user:
        return None
    is_valid, needs_rehash = _verify_password(password, user.get("password_hash", ""))
    if not is_valid:
        return None
    if needs_rehash:
        user["password_hash"] = _hash_password(password)
        _save_users(users)
    return user


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
    is_valid, _ = _verify_password(old_password, user.get("password_hash", ""))
    if not is_valid:
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
