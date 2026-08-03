"""Check registry.

Adding a course means adding checks here, not editing workflow YAML.
Every check has the same signature: (target, arg, repo_root) -> CheckResult.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import tomllib
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import yaml

CheckFn = Callable[[Path, str | None, Path], "CheckResult"]
REGISTRY: dict[str, CheckFn] = {}


@dataclass
class CheckResult:
    id: str
    passed: bool
    detail: str
    target: str = ""
    blocking: bool = True

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def check(name: str) -> Callable[[CheckFn], CheckFn]:
    def deco(fn: CheckFn) -> CheckFn:
        REGISTRY[name] = fn
        return fn

    return deco


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _git(repo_root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo_root, capture_output=True, text=True, check=False
    ).stdout


# --------------------------------------------------------------------------
# Generic artifact checks
# --------------------------------------------------------------------------


@check("exists")
def _exists(target: Path, arg: str | None, root: Path) -> CheckResult:
    ok = target.is_file()
    return CheckResult("exists", ok, "found" if ok else f"missing: {target}")


@check("min_words")
def _min_words(target: Path, arg: str | None, root: Path) -> CheckResult:
    if not target.is_file():
        return CheckResult("min_words", False, "file missing")
    n = len(_read(target).split())
    need = int(arg or 0)
    return CheckResult("min_words", n >= need, f"{n} words, need {need}")


@check("yaml_valid")
def _yaml_valid(target: Path, arg: str | None, root: Path) -> CheckResult:
    try:
        yaml.safe_load(_read(target))
        return CheckResult("yaml_valid", True, "parsed")
    except Exception as e:  # noqa: BLE001
        return CheckResult("yaml_valid", False, f"parse error: {e}")


@check("toml_valid")
def _toml_valid(target: Path, arg: str | None, root: Path) -> CheckResult:
    try:
        tomllib.loads(_read(target))
        return CheckResult("toml_valid", True, "parsed")
    except Exception as e:  # noqa: BLE001
        return CheckResult("toml_valid", False, f"parse error: {e}")


# Catches the student who committed the template unchanged. Cheap, and it
# catches more people than you would expect.
BOILERPLATE = (
    r"<!--\s*TODO",
    r"\bTBD\b",
    r"\bLorem ipsum\b",
    r"\[replace this\]",
    r"REPLACE_ME",
    r"^\s*Describe .*here\.?\s*$",
)


@check("not_template_boilerplate")
def _no_boilerplate(target: Path, arg: str | None, root: Path) -> CheckResult:
    if not target.is_file():
        return CheckResult("not_template_boilerplate", False, "file missing")
    text = _read(target)
    hits = [p for p in BOILERPLATE if re.search(p, text, re.IGNORECASE | re.MULTILINE)]
    return CheckResult(
        "not_template_boilerplate",
        not hits,
        "clean" if not hits else f"unedited template markers: {hits}",
    )


# --------------------------------------------------------------------------
# ADR checks
# --------------------------------------------------------------------------


def _has_heading(text: str, name: str) -> bool:
    return bool(re.search(rf"^#{{1,4}}\s*{re.escape(name)}\b", text, re.IGNORECASE | re.MULTILINE))


@check("adr_has_section")
def _adr_section(target: Path, arg: str | None, root: Path) -> CheckResult:
    if not target.is_file():
        return CheckResult("adr_has_section", False, "file missing")
    ok = _has_heading(_read(target), arg or "")
    return CheckResult("adr_has_section", ok, f"section '{arg}' {'present' if ok else 'absent'}")


@check("adr_has_rejected_alternative")
def _adr_rejected(target: Path, arg: str | None, root: Path) -> CheckResult:
    """An ADR without a rejected alternative is a changelog entry, not a decision."""
    if not target.is_file():
        return CheckResult("adr_has_rejected_alternative", False, "file missing")
    text = _read(target)
    heading = next(
        (
            h
            for h in ("Alternatives Considered", "Rejected", "Alternatives", "Options Considered")
            if _has_heading(text, h)
        ),
        None,
    )
    if heading is None:
        return CheckResult(
            "adr_has_rejected_alternative",
            False,
            "no alternatives section. An ADR with one option is not a decision.",
        )
    body = re.split(rf"^#{{1,4}}\s*{re.escape(heading)}\b", text, flags=re.I | re.M)[-1]
    body = re.split(r"^#{1,4}\s", body, flags=re.M)[0]
    words = len(body.split())
    ok = words >= 60
    return CheckResult(
        "adr_has_rejected_alternative",
        ok,
        f"'{heading}' section has {words} words, need 60",
    )


@check("runbook_has_section")
def _runbook_section(target: Path, arg: str | None, root: Path) -> CheckResult:
    if not target.is_file():
        return CheckResult("runbook_has_section", False, "file missing")
    ok = _has_heading(_read(target), arg or "")
    return CheckResult("runbook_has_section", ok, f"section '{arg}' {'present' if ok else 'absent'}")


# --------------------------------------------------------------------------
# Config checks
# --------------------------------------------------------------------------


@check("precommit_has_hooks")
def _precommit_hooks(target: Path, arg: str | None, root: Path) -> CheckResult:
    if not target.is_file():
        return CheckResult("precommit_has_hooks", False, "file missing")
    try:
        doc = yaml.safe_load(_read(target)) or {}
    except Exception as e:  # noqa: BLE001
        return CheckResult("precommit_has_hooks", False, f"parse error: {e}")
    present = {
        h.get("id", "")
        for repo in doc.get("repos", [])
        for h in repo.get("hooks", [])
    }
    blob = " ".join(present) + " " + " ".join(r.get("repo", "") for r in doc.get("repos", []))
    want = [w.strip() for w in (arg or "").split(",") if w.strip()]
    missing = [w for w in want if w not in blob]
    return CheckResult(
        "precommit_has_hooks",
        not missing,
        "all present" if not missing else f"missing hooks: {missing}",
    )


@check("pyproject_strict_mypy")
def _strict_mypy(target: Path, arg: str | None, root: Path) -> CheckResult:
    if not target.is_file():
        return CheckResult("pyproject_strict_mypy", False, "file missing")
    try:
        doc = tomllib.loads(_read(target))
    except Exception as e:  # noqa: BLE001
        return CheckResult("pyproject_strict_mypy", False, f"parse error: {e}")
    strict = bool(doc.get("tool", {}).get("mypy", {}).get("strict"))
    return CheckResult(
        "pyproject_strict_mypy", strict, "strict=true" if strict else "[tool.mypy] strict not set"
    )


# --------------------------------------------------------------------------
# Integrity signals. These FLAG, they never fail a milestone on their own.
# --------------------------------------------------------------------------


@check("commit_cadence")
def _cadence(target: Path, arg: str | None, root: Path) -> CheckResult:
    """Eleven milestones landing in ninety minutes is a signal, not a verdict."""
    log = _git(root, "log", "--format=%at", "-n", "200").split()
    if len(log) < 3:
        return CheckResult("commit_cadence", False, "fewer than 3 commits in history", blocking=False)
    ts = sorted(int(x) for x in log)
    span_h = (ts[-1] - ts[0]) / 3600
    ok = span_h >= 2.0
    return CheckResult(
        "commit_cadence",
        ok,
        f"{len(ts)} commits over {span_h:.1f}h"
        + ("" if ok else " (compressed history, flagged for review)"),
        blocking=False,
    )


@check("author_committer_match")
def _author_match(target: Path, arg: str | None, root: Path) -> CheckResult:
    rows = _git(root, "log", "--format=%ae|%ce", "-n", "100").strip().splitlines()
    bad = [r for r in rows if "|" in r and r.split("|")[0] != r.split("|")[1]]
    ok = not bad
    return CheckResult(
        "author_committer_match",
        ok,
        "consistent" if ok else f"{len(bad)} commits with author != committer",
        blocking=False,
    )


@check("diff_entropy")
def _diff_entropy(target: Path, arg: str | None, root: Path) -> CheckResult:
    """Real work has false starts. A single enormous first commit does not."""
    stats = _git(root, "log", "--format=%H", "--shortstat", "-n", "50")
    changed = [int(m) for m in re.findall(r"(\d+) insertion", stats)]
    if len(changed) < 3:
        return CheckResult("diff_entropy", False, "insufficient history", blocking=False)
    largest = max(changed)
    total = sum(changed)
    ratio = largest / total if total else 1.0
    ok = ratio < 0.8
    return CheckResult(
        "diff_entropy",
        ok,
        f"largest commit is {ratio:.0%} of all insertions"
        + ("" if ok else " (single-dump pattern, flagged)"),
        blocking=False,
    )


@check("no_binary_over")
def _no_big_binary(target: Path, arg: str | None, root: Path) -> CheckResult:
    limit = int(arg or 2_097_152)
    offenders = [
        f"{p.relative_to(root)} ({p.stat().st_size})"
        for p in root.rglob("*")
        if p.is_file() and ".git/" not in str(p) and p.stat().st_size > limit
    ]
    return CheckResult(
        "no_binary_over",
        not offenders,
        "clean" if not offenders else f"oversized files: {offenders[:5]}",
        blocking=False,
    )


@check("sha256_manifest")
def _sha_manifest(target: Path, arg: str | None, root: Path) -> CheckResult:
    """Used from DFIR M03 onward. Re-verifies every hash in a custody manifest."""
    if not target.is_file():
        return CheckResult("sha256_manifest", False, "manifest missing")
    import json

    try:
        entries = json.loads(_read(target)).get("artifacts", [])
    except Exception as e:  # noqa: BLE001
        return CheckResult("sha256_manifest", False, f"manifest unreadable: {e}")
    bad: list[str] = []
    for e in entries:
        p = root / e["path"]
        if not p.is_file():
            bad.append(f"{e['path']}: absent")
            continue
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        if digest != e.get("sha256"):
            bad.append(f"{e['path']}: hash mismatch")
    return CheckResult(
        "sha256_manifest", not bad, "all hashes verified" if not bad else f"{len(bad)} failures: {bad[:5]}"
    )
