"""Milestone contract loading and evaluation.

Design note: policy lives in the contract (milestone.yaml), never in the
workflow YAML. Workflows run tools and report outcomes. This module decides
what those outcomes mean. That separation is what lets a course_version bump
change grading without touching a single workflow file.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from academy_validate.registry import REGISTRY, CheckResult


@dataclass(slots=True)
class ArtifactSpec:
    path: str
    kind: str
    checks: list[str]
    weight: float = 10.0


@dataclass(slots=True)
class CheckSpec:
    id: str
    blocking: bool = True
    weight: float = 10.0


@dataclass(slots=True)
class Contract:
    milestone_id: str
    course: str
    course_version: str
    title: str
    depends_on: list[str]
    artifacts: list[ArtifactSpec]
    required_checks: list[CheckSpec]
    integrity_signals: list[str]
    defense_required: bool
    defense_min_score: float
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def load(cls, contract_path: Path, schema_path: Path) -> Contract:
        doc = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        # Fail loud. A malformed contract is an org bug or tampering, and both
        # must stop the run rather than silently grade against defaults.
        jsonschema.validate(instance=doc, schema=schema)

        meta, spec = doc["metadata"], doc["spec"]
        defense = spec["defense"]
        return cls(
            milestone_id=meta["id"],
            course=meta["course"],
            course_version=meta["course_version"],
            title=meta["title"],
            depends_on=spec.get("depends_on", []),
            artifacts=[
                ArtifactSpec(
                    path=a["path"],
                    kind=a["kind"],
                    checks=a["checks"],
                    weight=float(a.get("weight", 10)),
                )
                for a in spec["required_artifacts"]
            ],
            required_checks=[
                CheckSpec(
                    id=c["id"],
                    blocking=bool(c.get("blocking", True)),
                    weight=float(c.get("weight", 10)),
                )
                for c in spec["required_checks"]
            ],
            integrity_signals=spec.get("integrity_signals", []),
            defense_required=bool(defense["required"]),
            defense_min_score=float(defense.get("min_score", 70)),
            raw=doc,
        )


@dataclass(slots=True)
class Evaluation:
    contract: Contract
    artifact_results: list[CheckResult]
    toolchain_results: list[CheckResult]
    integrity_flags: list[CheckResult]
    score: float
    verdict: str  # PASS | NEEDS_REVISION | FAIL

    def to_attestation(self) -> dict[str, Any]:
        """The claim handed to the platform.

        Deliberately NOT a grade. The platform re-derives the verdict after
        verifying provenance, and no milestone reaches COMPLETE without a
        coach defense session regardless of what this says.
        """
        return {
            "schema": "academy/attestation/v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "milestone_id": self.contract.milestone_id,
            "course": self.contract.course,
            "course_version": self.contract.course_version,
            "provenance": {
                "repository": os.environ.get("GITHUB_REPOSITORY"),
                "run_id": os.environ.get("GITHUB_RUN_ID"),
                "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
                "workflow_ref": os.environ.get("GITHUB_WORKFLOW_REF"),
                "head_sha": os.environ.get("PR_HEAD_SHA"),
                "base_sha": os.environ.get("PR_BASE_SHA"),
                "actor": os.environ.get("GITHUB_ACTOR"),
            },
            "claimed_score": round(self.score, 2),
            "claimed_verdict": self.verdict,
            "requires_defense": self.contract.defense_required,
            "results": {
                "artifacts": [r.as_dict() for r in self.artifact_results],
                "toolchain": [r.as_dict() for r in self.toolchain_results],
                "integrity_flags": [r.as_dict() for r in self.integrity_flags],
            },
        }


def evaluate(contract: Contract, repo_root: Path) -> Evaluation:
    artifact_results: list[CheckResult] = []
    earned = 0.0
    possible = 0.0

    for art in contract.artifacts:
        target = repo_root / art.path
        per_check_weight = art.weight / len(art.checks)
        for check_expr in art.checks:
            name, _, arg = check_expr.partition(":")
            fn = REGISTRY.get(name)
            if fn is None:
                artifact_results.append(
                    CheckResult(
                        id=check_expr,
                        target=art.path,
                        passed=False,
                        detail=f"unknown check '{name}' (org bug, contact instructor)",
                    )
                )
                possible += per_check_weight
                continue
            result = fn(target, arg or None, repo_root)
            result.target = art.path
            artifact_results.append(result)
            possible += per_check_weight
            if result.passed:
                earned += per_check_weight

    # Toolchain outcomes arrive as env vars set by the reusable workflow.
    toolchain_results: list[CheckResult] = []
    for spec in contract.required_checks:
        outcome = os.environ.get(f"CHECK_{spec.id.upper()}", "missing")
        passed = outcome == "success"
        toolchain_results.append(
            CheckResult(
                id=spec.id,
                target="<toolchain>",
                passed=passed,
                detail=f"step outcome: {outcome}",
                blocking=spec.blocking,
            )
        )
        possible += spec.weight
        if passed:
            earned += spec.weight

    integrity_flags = [
        r
        for sig in contract.integrity_signals
        if (fn := REGISTRY.get(sig.partition(":")[0]))
        and not (r := fn(repo_root, sig.partition(":")[2] or None, repo_root)).passed
    ]

    score = (earned / possible * 100) if possible else 0.0

    blocking_failed = any(
        not r.passed for r in toolchain_results if r.blocking
    ) or any(not r.passed for r in artifact_results if r.blocking)

    if blocking_failed or score < 60:
        verdict = "FAIL" if score < 40 else "NEEDS_REVISION"
    else:
        verdict = "PASS"

    return Evaluation(
        contract=contract,
        artifact_results=artifact_results,
        toolchain_results=toolchain_results,
        integrity_flags=integrity_flags,
        score=score,
        verdict=verdict,
    )
