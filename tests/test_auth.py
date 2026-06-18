"""Unit tests for auth.py — bcrypt hashing, legacy migration, user management."""
import json

import pytest


@pytest.fixture
def auth_mod(tmp_path, monkeypatch):
    import auth
    monkeypatch.setattr(auth, "USERS_FILE", tmp_path / "users.json")
    return auth


def test_hash_is_bcrypt_and_round_trips(auth_mod):
    h = auth_mod._hash_password("secret")
    assert h.startswith("$2")
    ok, rehash = auth_mod._verify_password("secret", h)
    assert ok and not rehash
    assert auth_mod._verify_password("wrong", h) == (False, False)
    assert auth_mod._verify_password("x", "") == (False, False)


def test_legacy_sha256_verifies_and_flags_rehash(auth_mod):
    legacy = auth_mod._legacy_sha256("pw")
    ok, rehash = auth_mod._verify_password("pw", legacy)
    assert ok and rehash
    assert auth_mod._verify_password("nope", legacy) == (False, False)


def test_init_creates_admin_from_env(auth_mod, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "letmein")
    auth_mod.init_users()
    users = json.loads(auth_mod.USERS_FILE.read_text())
    assert users["admin"]["role"] == "admin"
    assert auth_mod.authenticate("admin", "letmein") is not None


def test_authenticate_migrates_legacy_hash(auth_mod):
    auth_mod.USERS_FILE.write_text(json.dumps({
        "bob": {"password_hash": auth_mod._legacy_sha256("pw"), "name": "Bob", "role": "user"}
    }))
    assert auth_mod.authenticate("bob", "pw") is not None
    # hash upgraded in place
    assert json.loads(auth_mod.USERS_FILE.read_text())["bob"]["password_hash"].startswith("$2")
    assert auth_mod.authenticate("bob", "wrong") is None


def test_user_management_lifecycle(auth_mod):
    auth_mod.init_users()
    assert auth_mod.add_user("carol", "pw12", "Carol", "user")[0] is True
    assert auth_mod.add_user("carol", "pw12", "Carol")[0] is False        # duplicate
    assert auth_mod.add_user("ab", "pw12", "x")[0] is False               # username too short
    assert auth_mod.add_user("dave", "p", "x")[0] is False                # password too short
    assert auth_mod.change_password("carol", "pw12", "newpw")[0] is True
    assert auth_mod.change_password("carol", "wrong", "abcd")[0] is False
    assert auth_mod.authenticate("carol", "newpw") is not None
    assert auth_mod.delete_user("carol")[0] is True
    assert auth_mod.delete_user("admin")[0] is False                      # admin protected
