"""Generator output smoke/regression test.

Mocks the Claude CLI and asserts the full set of scaffold files is produced with
the expected structure. Doubles as a guard against regressions when refactoring
generator.py.
"""
from pathlib import Path

import pytest

_SKILL_MD = (
    "# Skill\n" + "\n".join(f"line {i}" for i in range(90)) +
    "\n## Pre-conditions\n## Procedure\n## RALPH\n## Dashboard\n## Handoff\n"
)


@pytest.fixture
def gen(monkeypatch):
    import generator
    monkeypatch.setattr(generator, "run_claude_code", lambda *a, **k: {"text": _SKILL_MD, "error": False})
    return generator


def _project():
    skills = [
        {"name": "Alpha", "description": "first", "skill_file": "01_alpha.md"},
        {"name": "Beta", "description": "second", "skill_file": "02_beta.md"},
    ]
    return {
        "name": "Test Agent", "description": "a test", "target_platform": "claude_code",
        "form_fields": [
            {"id": "url", "label": "URL", "type": "text"},
            {"id": "token", "label": "Token", "type": "password", "is_secret": True},
        ],
        "skills": skills, "sub_agents": skills,
        "tools_needed": ["git"], "mcp_servers": [],
        "dashboard_metrics": [{"id": "progress", "label": "Progress", "type": "progress_bar"}],
        "form_values": {"url": "http://x"}, "secret_values": {"token": "abc"},
    }


def test_generates_expected_scaffold(gen, tmp_path):
    files, project_dir = gen.generate_project_files(_project(), "abcd1234", tmp_path)
    pdir = Path(project_dir)
    for expected in ["CLAUDE.md", "config.json", "dashboard.html", "run_server.py"]:
        assert (pdir / expected).exists(), f"missing {expected}"
    skill_files = list(pdir.glob("skills/*/SKILL.md"))
    assert len(skill_files) >= 2          # one per skill (+ dashboard protocol)

    # Secrets must NOT be written into config.json — only an ${ENV:...} reference.
    config = (pdir / "config.json").read_text()
    assert "abc" not in config
    assert "${ENV:TOKEN}" in config


def test_progress_callback_reports(gen, tmp_path):
    seen = []
    gen.generate_project_files(_project(), "efgh5678", tmp_path,
                               progress_cb=lambda pct, msg: seen.append(pct))
    assert seen and max(seen) >= 82       # reaches the post-skills milestone
