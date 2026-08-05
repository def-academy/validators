"""Regression tests for the advisory locked-path check.

The bug these exist to prevent: the original inline script hashed file content
with sha256 and compared it against the git blob SHAs stored in the lockfile.
Those values can never be equal, so the check reported MODIFIED on four untouched
files in every student repo on every push, and nothing in the suite noticed
because the logic lived inside workflow YAML.

``test_pristine_tree_passes`` is the one that would have caught it.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from academy_validate.lockcheck import (
    DELETED,
    MODIFIED,
    check_locked_paths,
    git_blob_sha,
    main,
)

LOCKED = {
    ".github/workflows/m01-validate.yml": "name: cti-m01 validate\n",
    "milestones/m01-foundation/milestone.yaml": "apiVersion: academy/v1\n",
    "schemas/milestone.schema.json": '{"type": "object"}\n',
}


def _build_repo(root: Path, *, values: dict[str, str] | None = None) -> Path:
    """Write the locked files plus a lockfile holding their real blob SHAs."""
    for path, content in LOCKED.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    locked_paths = values or {
        path: git_blob_sha(content.encode()) for path, content in LOCKED.items()
    }
    lockfile = root / ".academy" / "lockfile.json"
    lockfile.parent.mkdir(parents=True, exist_ok=True)
    lockfile.write_text(json.dumps({"locked_paths": locked_paths}), encoding="utf-8")
    return lockfile


# --------------------------------------------------------------------------
# The hash function itself
# --------------------------------------------------------------------------


def test_empty_blob_matches_the_well_known_git_constant():
    # `git hash-object -t blob /dev/null` on any git installation, ever.
    assert git_blob_sha(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_agrees_with_git_hash_object(tmp_path):
    """Independent oracle: the real git binary, not our own implementation."""
    for content in (b"", b"a", b"hello world\n", b"\x00\x01\x02binary\xff", b"x" * 5000):
        f = tmp_path / "blob.bin"
        f.write_bytes(content)
        expected = subprocess.run(
            ["git", "hash-object", "--", str(f)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert git_blob_sha(content) == expected, content[:20]


def test_blob_sha_is_not_a_content_sha256():
    """The exact confusion that caused the original bug, pinned as a test."""
    data = b"name: cti-m01 validate\n"
    assert git_blob_sha(data) != hashlib.sha256(data).hexdigest()
    assert len(git_blob_sha(data)) == 40
    assert len(hashlib.sha256(data).hexdigest()) == 64


# --------------------------------------------------------------------------
# check_locked_paths
# --------------------------------------------------------------------------


def test_pristine_tree_passes(tmp_path):
    """THE regression. An untouched repo must produce zero findings."""
    lockfile = _build_repo(tmp_path)
    assert check_locked_paths(lockfile, tmp_path) == []


def test_modified_file_is_reported_once(tmp_path):
    lockfile = _build_repo(tmp_path)
    target = tmp_path / "milestones/m01-foundation/milestone.yaml"
    target.write_text(target.read_text() + "  min_words: 10\n", encoding="utf-8")

    findings = check_locked_paths(lockfile, tmp_path)
    assert findings == [f"milestones/m01-foundation/milestone.yaml: {MODIFIED}"]


def test_single_byte_change_is_detected(tmp_path):
    lockfile = _build_repo(tmp_path)
    target = tmp_path / "schemas/milestone.schema.json"
    target.write_bytes(target.read_bytes().replace(b"object", b"objecT"))
    assert check_locked_paths(lockfile, tmp_path) == [f"schemas/milestone.schema.json: {MODIFIED}"]


def test_deleted_file_is_reported(tmp_path):
    lockfile = _build_repo(tmp_path)
    (tmp_path / ".github/workflows/m01-validate.yml").unlink()

    findings = check_locked_paths(lockfile, tmp_path)
    assert findings == [f".github/workflows/m01-validate.yml: {DELETED}"]


def test_multiple_findings_are_all_reported(tmp_path):
    lockfile = _build_repo(tmp_path)
    (tmp_path / ".github/workflows/m01-validate.yml").unlink()
    (tmp_path / "schemas/milestone.schema.json").write_text("{}", encoding="utf-8")

    assert sorted(check_locked_paths(lockfile, tmp_path)) == sorted([
        f".github/workflows/m01-validate.yml: {DELETED}",
        f"schemas/milestone.schema.json: {MODIFIED}",
    ])


@pytest.mark.parametrize("sentinel", ["PLACEHOLDER", "PLACEHOLDER_SHA256"])
def test_placeholder_entries_are_skipped(tmp_path, sentinel):
    lockfile = _build_repo(tmp_path, values=dict.fromkeys(LOCKED, sentinel))
    (tmp_path / "schemas/milestone.schema.json").write_text("anything", encoding="utf-8")
    assert check_locked_paths(lockfile, tmp_path) == []


def test_placeholder_still_reports_deletion(tmp_path):
    """A placeholder waives the hash comparison, not the file's existence."""
    lockfile = _build_repo(tmp_path, values=dict.fromkeys(LOCKED, "PLACEHOLDER"))
    (tmp_path / "schemas/milestone.schema.json").unlink()
    assert check_locked_paths(lockfile, tmp_path) == [f"schemas/milestone.schema.json: {DELETED}"]


def test_repo_root_defaults_to_lockfile_grandparent(tmp_path):
    lockfile = _build_repo(tmp_path)
    assert check_locked_paths(lockfile) == []


# --------------------------------------------------------------------------
# CLI entry point
# --------------------------------------------------------------------------


def test_main_returns_zero_on_clean_tree(tmp_path, capsys):
    _build_repo(tmp_path)
    assert main([str(tmp_path)]) == 0
    assert "Locked files intact." in capsys.readouterr().out


def test_main_returns_one_and_annotates_on_drift(tmp_path, capsys):
    _build_repo(tmp_path)
    (tmp_path / "schemas/milestone.schema.json").write_text("{}", encoding="utf-8")

    assert main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "::error::Locked files changed." in out
    assert f"::error::schemas/milestone.schema.json: {MODIFIED}" in out


def test_main_returns_one_when_lockfile_absent(tmp_path, capsys):
    assert main([str(tmp_path)]) == 1
    assert "not found" in capsys.readouterr().out
