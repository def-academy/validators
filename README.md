# def-academy/validators

Org-owned grading toolchain. Students call these workflows by `uses:` and cannot
modify them.

```
.github/workflows/
  cti-m01.yml      reusable validator, called from student repos
  _integrity.yml   advisory locked-file check
academy_validate/
  contract.py      contract loading and evaluation. Policy lives here
  registry.py      check implementations. Add a course by adding checks
  cli.py           academy-validate
  publish.py       academy-publish-check (Check Runs API)
platform/
  verify_workflow_run.py   AUTHORITATIVE verification. The trust boundary
contracts/
  allowlist.json   validator SHAs the platform will accept
```

## Release discipline

Cutting a tag is not enough. The platform trusts **SHAs**, not refs.

1. Merge to `main`
2. Tag `vX.Y.Z`
3. Append the commit SHA to `contracts/allowlist.json`
4. Deploy the allowlist. Until step 4, student runs on the new tag will fail
   verification with `validator SHA not allowlisted`, which is the correct
   failure mode

Never remove a SHA from the allowlist while any enrollment is pinned to a
course_version that references it.

## Local test

```bash
pip install -e .
cd path/to/student-repo
academy-validate --contract milestones/m01-foundation/milestone.yaml \
                 --schema schemas/milestone.schema.json \
                 --repo-root . --fail-on NEVER
```
