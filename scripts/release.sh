#!/usr/bin/env bash
# Release a course_version of def-academy/validators.
#
# Automates SCAFFOLD-WIRING step 2 — the step whose own docs note that skipping
# it makes every student run fail with "validator SHA not allowlisted", silently,
# until someone submits.
#
# ---------------------------------------------------------------------------
# THE TWO SHAs
# ---------------------------------------------------------------------------
# There are two, they are never equal, and confusing them is the whole reason
# this script exists.
#
#   S   the TAG SHA.  Student caller stubs say @v1.0.0. GitHub resolves that to
#       S and reports it in referenced_workflows[].sha. verify() assertion 2
#       checks S against the allowlist. This is the SHA that goes INTO
#       contracts/allowlist.json.
#
#   S'  the BUILD SHA. The commit that wrote S into allowlist.json. This is what
#       the platform image is built from (VALIDATORS_SHA in platform/.env).
#
# They cannot be equal: recording S in the allowlist produces a new commit, so
# the file can never contain the SHA of the commit containing it. S' ships the
# policy; S is what the policy permits.
#
# ---------------------------------------------------------------------------
# USAGE
#   scripts/release.sh v1.0.0 cti 1.0.0
#   scripts/release.sh --help
# ---------------------------------------------------------------------------

set -euo pipefail

usage() {
    sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

[[ "${1:-}" == "--help" || "${1:-}" == "-h" || $# -lt 3 ]] && usage

TAG="$1"          # v1.0.0
COURSE="$2"       # cti
VERSION="$3"      # 1.0.0
ALLOWLIST="contracts/allowlist.json"

cd "$(dirname "$0")/.."

[[ -f "$ALLOWLIST" ]] || { echo "ERROR: run from the validators repo (no $ALLOWLIST)" >&2; exit 1; }

if [[ -n "$(git status --porcelain)" ]]; then
    echo "ERROR: working tree is dirty. Commit or stash first — the tag must point" >&2
    echo "       at a commit that actually exists upstream." >&2
    exit 1
fi

# --- 1. tag, and resolve what the tag actually points at --------------------
echo "==> Tagging $TAG"
git tag -f "$TAG"
git push -f origin "$TAG"

# ^{} dereferences an annotated tag to the commit it wraps. Without it, an
# annotated tag resolves to the tag OBJECT's SHA, which is not what GitHub
# reports in referenced_workflows[] — a mismatch that fails every run.
S="$(git rev-parse "${TAG}^{}")"
echo "    tag SHA (S)  = $S"

# --- 2. write S into the allowlist ------------------------------------------
echo "==> Recording S in $ALLOWLIST for ${COURSE} ${VERSION}"
python3 - "$ALLOWLIST" "$COURSE" "$VERSION" "$TAG" "$S" <<'PY'
import json, sys
path, course, version, tag, sha = sys.argv[1:6]
doc = json.load(open(path))
entry = doc.setdefault(course, {}).setdefault(version, {})
entry.update({
    "tag": tag,
    "sha": sha,
    "workflows": entry.get("workflows") or [
        ".github/workflows/cti-m01.yml",
        ".github/workflows/_integrity.yml",
    ],
})
with open(path, "w", encoding="utf-8") as f:
    json.dump(doc, f, indent=2)
    f.write("\n")
print(f"    {course} {version} -> {sha}")
PY

if [[ -z "$(git status --porcelain "$ALLOWLIST")" ]]; then
    echo "    allowlist already current, no commit needed"
else
    git add "$ALLOWLIST"
    git commit -m "allowlist: ${COURSE} ${VERSION} -> ${S}"
    git push origin HEAD
fi

# --- 3. the build SHA -------------------------------------------------------
S_PRIME="$(git rev-parse HEAD)"

cat <<EOF

==> Done.

    tag   $TAG
    S     $S           <- in the allowlist; what students resolve to
    S'    $S_PRIME     <- build the platform from this

Next, in the platform repo:

    VALIDATORS_SHA=$S_PRIME    # in platform/.env
    docker compose build --no-cache && docker compose up -d

Verify the running image agrees:

    docker compose exec api printenv VALIDATORS_SHA
    docker compose exec api python -c "\\
import json; d=json.load(open('/app/contracts/allowlist.json')); \\
print(d['${COURSE}']['${VERSION}']['sha'])"

The first prints S' ($S_PRIME), the second prints S ($S).
Different values is correct. Identical values means something is wrong.
EOF
