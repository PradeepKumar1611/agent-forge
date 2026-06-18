"""Tests for Phase 4 features: rename, templates, versioning, per-skill regen."""
import time

import pytest

_LONG_MD = (
    "# Skill\n" + "\n".join(f"line {i}" for i in range(90)) +
    "\n## Pre-conditions\n## Procedure\n## RALPH\n## Dashboard\n## Handoff\n"
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    import auth
    import database
    import generator
    import server

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "f.db")
    database.init_db()
    tdir = tmp_path / "templates"
    tdir.mkdir()
    monkeypatch.setattr(server, "TEMPLATES_DIR", tdir)
    monkeypatch.setattr(auth, "_load_users",
                        lambda: {"alice": {"password_hash": "x", "name": "A", "role": "user"}})
    monkeypatch.setattr(server, "run_claude_code", lambda *a, **k: {"text": "{}", "error": False})
    monkeypatch.setattr(generator, "run_claude_code", lambda *a, **k: {"text": _LONG_MD, "error": False})
    server.app.config["TESTING"] = True
    client = server.app.test_client()
    with client.session_transaction() as s:
        s["username"] = "alice"
        s["role"] = "user"
        s["name"] = "A"
    return server, database, tmp_path, client


def _mk(database, pid="p1", owner="alice", **extra):
    base = {
        "id": pid, "name": "Proj", "description": "test project", "created_by": owner,
        "status": "form_filled", "is_public": False,
        "form_fields": [{"id": "x", "label": "X"}],
        "skills": [{"name": "Alpha", "description": "a"}],
        "sub_agents": [{"name": "Alpha", "description": "a"}],
        "tools_needed": [], "mcp_servers": [], "dashboard_metrics": [],
        "form_values": {}, "chat_history": [], "generated_files": [],
    }
    base.update(extra)
    database.create_project(base)


def _await_job(client, job_id, limit=100):
    job = None
    for _ in range(limit):
        job = client.get(f"/api/jobs/{job_id}").get_json()
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    return job


def test_rename(env):
    server, database, _, c = env
    _mk(database)
    r = c.post("/api/projects/p1/rename", json={"name": "Renamed"})
    assert r.status_code == 200 and r.get_json()["name"] == "Renamed"
    assert c.post("/api/projects/p1/rename", json={"name": "   "}).status_code == 400


def test_templates_save_list_get(env):
    server, database, _, c = env
    _mk(database)
    assert c.get("/api/templates").get_json() == []        # starts empty (isolated dir)
    r = c.post("/api/templates", json={"project_id": "p1"})
    assert r.status_code == 201
    slug = r.get_json()["slug"]
    assert any(t["slug"] == slug for t in c.get("/api/templates").get_json())
    spec = c.get(f"/api/templates/{slug}").get_json()
    assert spec["form_fields"] and (spec.get("skills") or spec.get("sub_agents"))


def test_template_save_requires_design(env):
    server, database, _, c = env
    _mk(database, skills=[], sub_agents=[])
    assert c.post("/api/templates", json={"project_id": "p1"}).status_code == 400


def test_versions_snapshot_and_restore(env):
    server, database, _, c = env
    _mk(database)
    job_id = c.post("/api/projects/p1/chat", json={"message": "tweak it"}).get_json()["job_id"]
    assert _await_job(c, job_id)["status"] == "done"
    versions = c.get("/api/projects/p1/versions").get_json()
    assert len(versions) >= 1
    assert c.post("/api/projects/p1/versions/0/restore", json={}).status_code == 200
    assert c.post("/api/projects/p1/versions/999/restore", json={}).status_code == 404


def test_regenerate_requires_generated_project(env):
    server, database, _, c = env
    _mk(database)  # no project_dir
    assert c.post("/api/projects/p1/skills/0/regenerate", json={}).status_code == 400


def test_regenerate_skill_writes_file(env):
    server, database, tmp_path, c = env
    pdir = tmp_path / "genproj"
    (pdir / "skills").mkdir(parents=True)
    _mk(database, project_dir=str(pdir), generated_files=["skills/alpha/SKILL.md"])
    job_id = c.post("/api/projects/p1/skills/0/regenerate", json={}).get_json()["job_id"]
    job = _await_job(c, job_id)
    assert job["status"] == "done", job
    assert (pdir / "skills" / "alpha" / "SKILL.md").exists()
