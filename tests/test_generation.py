"""Tests for Phase 2 per-skill generation + quality gate (no real Claude CLI)."""
import generator


def _good_md():
    body = "\n".join(f"detail line {i}" for i in range(90))
    return (
        "# Skill\n" + body +
        "\n## Pre-conditions\n## Detailed Procedure\n## RALPH Self-Healing Loop\n"
        "## Dashboard State Updates\n## Handoff\n"
    )


def test_strip_code_fences():
    assert generator._strip_code_fences("```markdown\n# Hi\n```") == "# Hi"
    assert generator._strip_code_fences("# Plain") == "# Plain"


def test_quality_bar():
    assert generator._meets_quality_bar(_good_md()) is True
    assert generator._meets_quality_bar("# Tiny\nnope") is False
    assert generator._meets_quality_bar("") is False


def test_skill_filename_matches_write_loop():
    assert generator._skill_filename({"skill_file": "01_scan.md"}) == "01_scan.md"
    assert generator._skill_filename({"name": "My Skill"}) == "my skill.md".replace(" ", "_")


def test_one_call_per_skill_when_quality_passes(monkeypatch):
    calls = []
    monkeypatch.setattr(generator, "run_claude_code",
                        lambda *a, **k: calls.append(1) or {"text": _good_md(), "error": False})
    sub = [{"name": "Alpha", "description": "a"},
           {"name": "Beta", "description": "b"},
           {"name": "Gamma", "description": "c"}]
    out = generator._generate_all_skills({"name": "P", "description": "d"}, sub, [], {}, "pid")
    assert len(out) == 3              # one file per skill, no fixed cap
    assert len(calls) == 3           # no retries needed → exactly one call each


def test_quality_gate_triggers_retry(monkeypatch):
    calls = []
    monkeypatch.setattr(generator, "run_claude_code",
                        lambda *a, **k: calls.append(1) or {"text": "# Tiny\nshort", "error": False})
    sub = [{"name": "Solo", "description": "x"}]
    generator._generate_all_skills({"name": "P", "description": "d"}, sub, [], {}, "pid")
    assert len(calls) == 2           # short output → exactly one quality-gated retry


def test_cli_error_skips_skill(monkeypatch):
    monkeypatch.setattr(generator, "run_claude_code",
                        lambda *a, **k: {"text": "boom", "error": True})
    sub = [{"name": "X", "description": "y"}]
    out = generator._generate_all_skills({"name": "P", "description": ""}, sub, [], {}, "pid")
    assert out == {}                 # error → None → caller falls back to template


def test_empty_roster():
    assert generator._generate_all_skills({"name": "P"}, [], [], {}, "pid") == {}
