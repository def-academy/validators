"""Regression suite. The validator grades people; it gets tests."""
from pathlib import Path

import pytest

from academy_validate.registry import REGISTRY


def w(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_min_words(tmp_path):
    p = w(tmp_path, "a.md", "one two three")
    assert REGISTRY["min_words"](p, "3", tmp_path).passed
    assert not REGISTRY["min_words"](p, "4", tmp_path).passed


def test_missing_file_never_crashes(tmp_path):
    """Every check must handle absence. A student who forgot a file gets
    feedback, not a stack trace."""
    ghost = tmp_path / "nope.md"
    for name in ("exists", "min_words", "adr_has_section", "adr_has_rejected_alternative",
                 "not_template_boilerplate", "precommit_has_hooks", "pyproject_strict_mypy",
                 "runbook_has_section"):
        r = REGISTRY[name](ghost, "10", tmp_path)
        assert r.passed is False


def test_adr_rejects_single_option(tmp_path):
    p = w(tmp_path, "adr.md", "## Context\nx\n## Decision\nWe use X.\n")
    assert not REGISTRY["adr_has_rejected_alternative"](p, None, tmp_path).passed


def test_adr_rejects_thin_alternatives(tmp_path):
    p = w(tmp_path, "adr.md", "## Alternatives Considered\nY is worse.\n")
    r = REGISTRY["adr_has_rejected_alternative"](p, None, tmp_path)
    assert not r.passed and "60" in r.detail


def test_adr_accepts_substantive_alternatives(tmp_path):
    p = w(tmp_path, "adr.md", "## Alternatives Considered\n" + ("word " * 70))
    assert REGISTRY["adr_has_rejected_alternative"](p, None, tmp_path).passed


@pytest.mark.parametrize("text", ["<!-- TODO: fill in -->", "REPLACE_ME", "Lorem ipsum dolor"])
def test_boilerplate_detected(tmp_path, text):
    p = w(tmp_path, "b.md", text)
    assert not REGISTRY["not_template_boilerplate"](p, None, tmp_path).passed


def test_precommit_hook_detection(tmp_path):
    p = w(tmp_path, "pc.yaml", "repos:\n  - repo: https://github.com/astral-sh/ruff-pre-commit\n"
                               "    hooks:\n      - id: ruff\n")
    assert REGISTRY["precommit_has_hooks"](p, "ruff", tmp_path).passed
    assert not REGISTRY["precommit_has_hooks"](p, "ruff,gitleaks", tmp_path).passed


def test_integrity_signals_are_non_blocking(tmp_path):
    """Signals inform an instructor. They must never auto-fail a student."""
    for name in ("commit_cadence", "diff_entropy", "author_committer_match"):
        assert REGISTRY[name](tmp_path, None, tmp_path).blocking is False
