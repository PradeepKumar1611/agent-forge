"""Regression tests for project access control.

Before Phase 1, every by-id project route except /visibility skipped ownership
checks, so any logged-in user could read, mutate, generate, or download another
user's project (including its secrets). These tests pin that closed.

Run with:  ./venv/bin/python -m pytest tests/ -q
(invoked via `python -m` so the project root is importable)
"""
import time

import pytest


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    import auth
    import database
    import server

    # Isolate the DB to a temp file (every query reads DB_PATH at call time).
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    database.init_db()

    # Fake the user store so we can drive sessions without touching users.json.
    fake_users = {
        "alice": {"password_hash": "x", "name": "Alice", "role": "user"},
        "bob": {"password_hash": "x", "name": "Bob", "role": "user"},
        "admin": {"password_hash": "x", "name": "Admin", "role": "admin"},
    }
    monkeypatch.setattr(auth, "_load_users", lambda: fake_users)

    # Never spawn the real Claude CLI from a unit test.
    monkeypatch.setattr(server, "run_claude_code", lambda *a, **k: {"text": "{}", "error": False})

    server.app.config["TESTING"] = True
    return server, database, server.app.test_client()


def _new_project(database, pid, owner, public=False):
    database.create_project({
        "id": pid, "name": f"{owner} project", "created_by": owner,
        "status": "form_filled", "is_public": public,
        "form_fields": [], "skills": [], "sub_agents": [], "tools_needed": [],
        "mcp_servers": [], "dashboard_metrics": [], "form_values": {},
        "chat_history": [], "generated_files": [],
    })


def _login(client, username, role="user"):
    with client.session_transaction() as sess:
        sess["username"] = username
        sess["role"] = role
        sess["name"] = username


# Routes a non-owner must NOT be able to touch on a PRIVATE project.
WRITE_ROUTES = [
    ("POST", "/api/projects/p1/tags", {"tags": ["x"]}),
    ("POST", "/api/projects/p1/visibility", {"is_public": True}),
    ("POST", "/api/projects/p1/describe", {"description": "hi"}),
    ("POST", "/api/projects/p1/chat", {"message": "hi"}),
    ("POST", "/api/projects/p1/form-values", {"values": {}}),
    ("POST", "/api/projects/p1/design-dashboard", {}),
    ("POST", "/api/projects/p1/generate", {}),
    ("DELETE", "/api/projects/p1", None),
]
READ_ROUTES = [
    ("GET", "/api/projects/p1"),
    ("GET", "/api/projects/p1/download"),
    ("GET", "/api/projects/p1/state"),
    ("GET", "/api/projects/p1/export"),
]


def test_other_user_blocked_on_private_project(app_client):
    server, database, client = app_client
    _new_project(database, "p1", "alice", public=False)
    _login(client, "bob")

    for method, path, body in WRITE_ROUTES:
        resp = client.open(path, method=method, json=(body if body is not None else {}))
        assert resp.status_code == 403, f"{method} {path} should be 403, got {resp.status_code}"

    for method, path in READ_ROUTES:
        resp = client.open(path, method=method)
        assert resp.status_code == 403, f"{method} {path} should be 403, got {resp.status_code}"


def test_owner_allowed(app_client):
    server, database, client = app_client
    _new_project(database, "p1", "alice", public=False)
    _login(client, "alice")

    assert client.get("/api/projects/p1").status_code == 200
    # describe is now async: owner is allowed → 202 with a job id (work runs in a thread)
    r = client.post("/api/projects/p1/describe", json={"description": "build a thing"})
    assert r.status_code == 202
    assert "job_id" in r.get_json()


def test_admin_allowed(app_client):
    server, database, client = app_client
    _new_project(database, "p1", "alice", public=False)
    _login(client, "admin", role="admin")
    assert client.get("/api/projects/p1").status_code == 200
    assert client.post("/api/projects/p1/tags", json={"tags": ["ok"]}).status_code == 200


def test_public_project_readable_but_not_writable(app_client):
    server, database, client = app_client
    _new_project(database, "p1", "alice", public=True)
    _login(client, "bob")
    # public → readable
    assert client.get("/api/projects/p1").status_code == 200
    # but still not mutable by a non-owner
    assert client.post("/api/projects/p1/describe", json={"description": "x"}).status_code == 403


def test_delete_removes_project(app_client):
    server, database, client = app_client
    _new_project(database, "p1", "alice", public=False)
    _login(client, "alice")
    assert client.delete("/api/projects/p1").status_code == 200
    assert database.get_project("p1") is None


def test_missing_project_is_404(app_client):
    server, database, client = app_client
    _login(client, "alice")
    assert client.get("/api/projects/nope").status_code == 404


def test_async_job_lifecycle(app_client):
    """describe returns 202+job_id; polling /api/jobs/<id> reaches done with a result."""
    server, database, client = app_client
    _new_project(database, "p1", "alice")
    _login(client, "alice")

    job_id = client.post("/api/projects/p1/describe", json={"description": "x"}).get_json()["job_id"]
    job = None
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").get_json()
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert job["status"] == "done", job
    assert job["result"] is not None
    assert "form_fields" in job["result"]


def test_job_not_visible_to_other_user(app_client):
    server, database, client = app_client
    _new_project(database, "p1", "alice")
    _login(client, "alice")
    job_id = client.post("/api/projects/p1/describe", json={"description": "x"}).get_json()["job_id"]
    _login(client, "bob")
    assert client.get(f"/api/jobs/{job_id}").status_code == 403
