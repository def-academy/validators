"""Locked-path verification for the advisory in-repo integrity check.

Advisory tier. The authoritative comparison lives in
``platform_verify.verify_workflow_run.verify()`` assertion 3, which compares the
GitHub contents API ``sha`` against the blob SHAs captured in
``enrollments.template_blobs`` at enrollment — server-side, on data the student
cannot reach. This module exists so an honest student gets the same answer early,
from a check they can read, instead of discovering it from the platform after
spending a submission.

The values in ``.academy/lockfile.json`` are **git blob SHAs**: what
``git hash-object`` prints and what the contents API returns as ``sha``. They are
``sha1(b"blob <len>\\0" + content)``, not a plain content hash. A ``sha256`` of the
raw bytes is a different length, a different algorithm, and will never compare
equal for any input — which is exactly how this check spent its first release
reporting MODIFIED on four untouched files in every student repo. That is the
regression ``tests/test_lockcheck.py`` exists to prevent.

This lives in the org-owned validators repo, not in the student template, so a
student cannot silently weaken it. It is still advisory: a student who edits a
locked file can also edit their copy of the lockfile. That is understood and is
the reason the platform keeps its own copy.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

#: Lockfile entries with these values are skipped rather than compared. Used by
#: the template before the org generates real SHAs at course-version release.
#: ``PLACEHOLDER_SHA256`` is the legacy spelling, kept for one release — the name
#: encodes the wrong hash function and was a contributing cause of the original
#: bug. Drop it once no shipped template uses it.
PLACEHOLDERS = frozenset({"PLACEHOLDER", "PLACEHOLDER_SHA256"})

DELETED = "DELETED"
MODIFIED = "MODIFIED"


def git_blob_sha(data: bytes) -> str:
    """Return the git blob SHA of ``data``.

    Equivalent to ``git hash-object`` and to the ``sha`` field of the GitHub
    contents API. ``usedforsecurity=False`` keeps this importable under FIPS
    builds, where bare SHA-1 is otherwise refused; this is a content address, not
    a security primitive, and the security decision is made platform-side.
    """
    header = b"blob %d\0" % len(data)
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def check_locked_paths(lockfile: Path, repo_root: Path | None = None) -> list[str]:
    """Compare every locked path against its recorded blob SHA.

    Returns a list of ``"<path>: <STATUS>"`` strings, empty when everything
    matches. Paths in the lockfile are relative to the repo root, which defaults
    to the lockfile's grandparent (``<root>/.academy/lockfile.json``).
    """
    root = repo_root if repo_root is not None else lockfile.parent.parent
    doc = json.loads(lockfile.read_text(encoding="utf-8"))
    findings: list[str] = []

    for path, expected in doc["locked_paths"].items():
        target = root / path
        if not target.is_file():
            findings.append(f"{path}: {DELETED}")
            continue
        if expected in PLACEHOLDERS:
            continue
        if git_blob_sha(target.read_bytes()) != expected:
            findings.append(f"{path}: {MODIFIED}")

    return findings


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else Path.cwd()
    lockfile = root / ".academy" / "lockfile.json"

    if not lockfile.is_file():
        print(f"::error::{lockfile} not found; cannot verify locked paths.")
        return 1

    findings = check_locked_paths(lockfile, root)
    if findings:
        print("::error::Locked files changed. This will fail platform-side verification.")
        for f in findings:
            print(f"::error::{f}")
        return 1

    print("Locked files intact.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
