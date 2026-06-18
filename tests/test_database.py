"""Unit tests for database.py — CRUD and ownership/visibility filtering."""
import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    import database
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "t.db")
    database.init_db()
    return database


def _mk(db, pid, owner, public=False, created_at="2026-01-01"):
    db.create_project({
        "id": pid, "created_by": owner, "name": pid, "status": "new",
        "is_public": public, "created_at": created_at,
    })


def test_crud_round_trip(db):
    _mk(db, "a", "alice")
    assert db.get_project("a")["name"] == "a"
    p = db.get_project("a")
    p["name"] = "a2"
    db.save_project(p)
    assert db.get_project("a")["name"] == "a2"
    db.delete_project("a")
    assert db.get_project("a") is None


def test_get_missing_returns_none(db):
    assert db.get_project("missing") is None


def test_admin_sees_all(db):
    _mk(db, "a", "alice")
    _mk(db, "b", "bob")
    assert len(db.list_projects_for_user("anyone", "admin")) == 2


def test_user_sees_own_plus_public(db):
    _mk(db, "a", "alice", public=False)
    _mk(db, "b", "bob", public=False)
    _mk(db, "c", "bob", public=True)
    alice_ids = {p["id"] for p in db.list_projects_for_user("alice", "user")}
    assert alice_ids == {"a", "c"}          # own + bob's public
    bob_ids = {p["id"] for p in db.list_projects_for_user("bob", "user")}
    assert bob_ids == {"b", "c"}            # both his own
