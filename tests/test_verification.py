"""The trust boundary is the part worth testing hardest."""
from platform_verify.verify_workflow_run import (
    Enrollment, Verdict, next_state, verify, verify_signature,
)

ENR = Enrollment(id="e1", repo_id=42, installation_id=99, course_version_id="1.0.0",
                 template_blobs={".github/workflows/m01-validate.yml": "blobA"})
GOOD_SHA = "a" * 40


class FakeGH:
    def __init__(self, referenced, blobs, att):
        self._r, self._b, self._a = referenced, blobs, att
    def get_workflow_run(self, repo_id, run_id):
        return {"referenced_workflows": self._r}
    def get_blob_sha(self, repo_id, path, ref):
        return self._b.get(path)
    def download_artifact_json(self, repo_id, run_id, name):
        return self._a


def payload(**over):
    p = {"workflow_run": {"id": 7, "status": "completed", "event": "pull_request",
                          "head_sha": "deadbeef", "head_repository": {"id": 42}},
         "repository": {"id": 42}, "installation": {"id": 99}}
    p["workflow_run"].update(over.pop("run", {}))
    p.update(over)
    return p


def good_att():
    return {"milestone_id": "cti-m01", "course_version": "1.0.0",
            "provenance": {"run_id": 7, "head_sha": "deadbeef"}}


def test_happy_path():
    gh = FakeGH([{"path": "def-academy/validators/.github/workflows/cti-m01.yml",
                  "sha": GOOD_SHA, "ref": "refs/tags/v1.0.0"}],
                {".github/workflows/m01-validate.yml": "blobA"}, good_att())
    assert verify(payload(), ENR, gh, {GOOD_SHA}).ok


def test_unallowlisted_validator_sha_is_held():
    """A mutable tag repointed at attacker code must not pass."""
    gh = FakeGH([{"path": "def-academy/validators/.github/workflows/cti-m01.yml",
                  "sha": "b" * 40, "ref": "refs/tags/v1.0.0"}],
                {".github/workflows/m01-validate.yml": "blobA"}, good_att())
    r = verify(payload(), ENR, gh, {GOOD_SHA})
    assert r.verdict is Verdict.INTEGRITY_HOLD and "not allowlisted" in r.reasons[0]


def test_student_swapped_in_own_grader():
    gh = FakeGH([{"path": "student/evil/.github/workflows/pass.yml", "sha": GOOD_SHA}],
                {".github/workflows/m01-validate.yml": "blobA"}, good_att())
    r = verify(payload(), ENR, gh, {GOOD_SHA})
    assert r.verdict is Verdict.INTEGRITY_HOLD


def test_edited_caller_stub_is_held():
    gh = FakeGH([{"path": "def-academy/validators/.github/workflows/cti-m01.yml", "sha": GOOD_SHA}],
                {".github/workflows/m01-validate.yml": "TAMPERED"}, good_att())
    r = verify(payload(), ENR, gh, {GOOD_SHA})
    assert any("locked file modified" in x for x in r.reasons)


def test_replayed_attestation_from_another_run():
    att = good_att(); att["provenance"]["run_id"] = 999
    gh = FakeGH([{"path": "def-academy/validators/.github/workflows/cti-m01.yml", "sha": GOOD_SHA}],
                {".github/workflows/m01-validate.yml": "blobA"}, att)
    r = verify(payload(), ENR, gh, {GOOD_SHA})
    assert any("run_id" in x for x in r.reasons)


def test_fork_origin_rejected():
    gh = FakeGH([{"path": "def-academy/validators/.github/workflows/cti-m01.yml", "sha": GOOD_SHA}],
                {".github/workflows/m01-validate.yml": "blobA"}, good_att())
    r = verify(payload(run={"head_repository": {"id": 1337}}), ENR, gh, {GOOD_SHA})
    assert any("fork" in x for x in r.reasons)


def test_advisory_integrity_run_is_ignored_not_held():
    """_integrity.yml uploads no attestation by design. Its completion races
    the real validator run on every PR push; if it were held, a student in
    VALIDATING would get a false INTEGRITY_HOLD needing human release."""
    gh = FakeGH([{"path": "def-academy/validators/.github/workflows/_integrity.yml",
                  "sha": GOOD_SHA, "ref": "refs/tags/v1.0.0"}],
                {".github/workflows/m01-validate.yml": "blobA"}, None)
    r = verify(payload(), ENR, gh, {GOOD_SHA})
    assert r.verdict is Verdict.IGNORED
    assert next_state(r, "") == "NO_CHANGE"


def test_advisory_verdict_ignores_even_unallowlisted_sha():
    """An advisory run can't grade, so it can't hold either — tampering with
    _integrity.yml only breaks the student's own feedback loop. The blob-level
    check on the caller stub still catches edits to locked files."""
    gh = FakeGH([{"path": "def-academy/validators/.github/workflows/_integrity.yml",
                  "sha": "b" * 40}],
                {".github/workflows/m01-validate.yml": "blobA"}, None)
    assert verify(payload(), ENR, gh, {GOOD_SHA}).verdict is Verdict.IGNORED


def test_grading_run_with_advisory_sibling_still_fully_verified():
    """A run referencing BOTH workflows is a grading run: every assertion
    applies, including the SHA allowlist on the advisory sibling."""
    gh = FakeGH([{"path": "def-academy/validators/.github/workflows/cti-m01.yml",
                  "sha": GOOD_SHA},
                 {"path": "def-academy/validators/.github/workflows/_integrity.yml",
                  "sha": "b" * 40}],
                {".github/workflows/m01-validate.yml": "blobA"}, good_att())
    r = verify(payload(), ENR, gh, {GOOD_SHA})
    assert r.verdict is Verdict.INTEGRITY_HOLD


def test_signature_verification():
    secret, body = b"s3cret", b'{"a":1}'
    import hmac, hashlib
    good = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    assert verify_signature(secret, body, good)
    assert not verify_signature(secret, body, "sha256=" + "0" * 64)
    assert not verify_signature(secret, body, None)


def test_passing_validation_never_yields_complete():
    """The whole pedagogy rests on this assertion."""
    from platform_verify.verify_workflow_run import VerificationResult
    r = VerificationResult(Verdict.VERIFIED, "cti-m01", [])
    assert next_state(r, "PASS") == "COACH_REVIEW"
