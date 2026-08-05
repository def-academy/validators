"""Authoritative, platform-side verification of a workflow_run webhook.

This is the trust boundary. Everything the student's repo produces is a CLAIM.
This module is the only thing that turns a claim into state.

Four assertions, in order of how hard they are to defeat:

  1. The check run was produced by OUR App installation, not a PAT or a fork.
  2. referenced_workflows[] resolves to an org-owned repo at an ALLOWLISTED SHA.
     Tag refs are mutable, so we never trust the ref string, only the SHA.
  3. The student's caller stub and milestone.yaml match the template blob SHAs
     recorded at enrollment.
  4. The attestation's self-reported provenance matches the run we actually saw.

Defeating this requires compromising an org repo or the App private key. It is
no longer a text edit, which was the entire point.
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Any, Protocol

log = logging.getLogger(__name__)

ORG = "def-academy"
VALIDATOR_REPO = f"{ORG}/validators"


class Verdict(StrEnum):
    VERIFIED = "VERIFIED"
    INTEGRITY_HOLD = "INTEGRITY_HOLD"
    IGNORED = "IGNORED"


@dataclass(frozen=True, slots=True)
class Enrollment:
    id: str
    repo_id: int
    installation_id: int
    course_version_id: str
    template_blobs: dict[str, str]  # path -> git blob SHA at enrollment


@dataclass(frozen=True, slots=True)
class VerificationResult:
    verdict: Verdict
    milestone_id: str | None
    reasons: list[str]

    @property
    def ok(self) -> bool:
        return self.verdict is Verdict.VERIFIED


class GitHubClient(Protocol):
    def get_workflow_run(self, repo_id: int, run_id: int) -> dict[str, Any]: ...
    def get_blob_sha(self, repo_id: int, path: str, ref: str) -> str | None: ...
    def download_artifact_json(self, repo_id: int, run_id: int, name: str) -> dict[str, Any] | None: ...


# ---------------------------------------------------------------------------
# Webhook signature. Do this before parsing the body, not after.
# ---------------------------------------------------------------------------


def verify_signature(secret: bytes, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret, body, sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def _is_grading_workflow(path: str) -> bool:
    """Advisory helpers are "_"-prefixed by convention and never produce grades."""
    name = path.split("@", 1)[0].rsplit("/", 1)[-1]
    return not name.startswith("_")


# ---------------------------------------------------------------------------
# The four assertions
# ---------------------------------------------------------------------------


def verify(
    payload: dict[str, Any],
    enrollment: Enrollment,
    gh: GitHubClient,
    allowlisted_validator_shas: set[str],
) -> VerificationResult:
    reasons: list[str] = []
    run = payload.get("workflow_run", {})
    run_id = run.get("id")
    repo_id = payload.get("repository", {}).get("id")

    if repo_id != enrollment.repo_id:
        return VerificationResult(Verdict.IGNORED, None, ["repo_id mismatch"])
    if run.get("status") != "completed":
        return VerificationResult(Verdict.IGNORED, None, ["run not completed"])

    # --- 1. Provenance of the App installation -----------------------------
    installation_id = payload.get("installation", {}).get("id")
    if installation_id != enrollment.installation_id:
        reasons.append(
            f"installation mismatch: saw {installation_id}, "
            f"expected {enrollment.installation_id}"
        )

    # A fork PR cannot be trusted to run our validator against our secrets.
    if run.get("event") == "pull_request" and run.get("head_repository", {}).get("id") != repo_id:
        reasons.append("run originated from a fork")

    # --- 2. Validator SHA allowlist ----------------------------------------
    # The API returns the RESOLVED sha for each reusable workflow. Tags lie,
    # SHAs do not.
    detail = gh.get_workflow_run(enrollment.repo_id, run_id)
    referenced = detail.get("referenced_workflows") or []

    org_refs = [w for w in referenced if str(w.get("path", "")).startswith(f"{VALIDATOR_REPO}/")]
    if not org_refs:
        reasons.append("no org-owned validator referenced: student ran their own grader")

    # Advisory runs never grade. "_"-prefixed workflows (_integrity.yml) exist
    # for fast in-repo feedback and upload no attestation; demanding one here
    # turned every advisory completion into a false INTEGRITY_HOLD that raced
    # the real validator run.
    if org_refs and not any(_is_grading_workflow(str(w.get("path", ""))) for w in org_refs):
        return VerificationResult(
            Verdict.IGNORED, None, ["advisory run: no grading workflow referenced"]
        )
    for w in org_refs:
        if w.get("sha") not in allowlisted_validator_shas:
            reasons.append(
                f"validator SHA not allowlisted: {w.get('path')}@{w.get('sha')} "
                f"(ref claimed: {w.get('ref')})"
            )

    # --- 3. Locked-file integrity ------------------------------------------
    head_sha = run.get("head_sha")
    for path, expected_blob in enrollment.template_blobs.items():
        actual = gh.get_blob_sha(enrollment.repo_id, path, head_sha)
        if actual is None:
            reasons.append(f"locked file deleted: {path}")
        elif actual != expected_blob:
            reasons.append(f"locked file modified: {path}")

    # --- 4. Attestation self-consistency -----------------------------------
    milestone_id: str | None = None
    att = gh.download_artifact_json(enrollment.repo_id, run_id, "attestation-*")
    if att is None:
        reasons.append("no attestation artifact uploaded")
    else:
        milestone_id = att.get("milestone_id")
        prov = att.get("provenance", {})
        if str(prov.get("run_id")) != str(run_id):
            reasons.append("attestation run_id does not match the observed run")
        if prov.get("head_sha") != head_sha:
            reasons.append("attestation head_sha does not match the observed run")
        if att.get("course_version") != enrollment.course_version_id:
            reasons.append("attestation course_version does not match enrollment pin")

    if reasons:
        log.warning("integrity hold on enrollment=%s run=%s: %s", enrollment.id, run_id, reasons)
        return VerificationResult(Verdict.INTEGRITY_HOLD, milestone_id, reasons)

    return VerificationResult(Verdict.VERIFIED, milestone_id, [])


# ---------------------------------------------------------------------------
# Transition. Note what this does NOT do: it never sets COMPLETE.
# ---------------------------------------------------------------------------


def next_state(result: VerificationResult, claimed_verdict: str) -> str:
    if result.verdict is Verdict.INTEGRITY_HOLD:
        return "INTEGRITY_HOLD"
    if result.verdict is Verdict.IGNORED:
        return "NO_CHANGE"
    if claimed_verdict == "PASS":
        return "COACH_REVIEW"  # a human-or-coach defense still gates COMPLETE
    return "NEEDS_REVISION"
