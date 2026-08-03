"""academy-validate entrypoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from academy_validate.contract import Contract, evaluate


def _summary(ev) -> str:  # noqa: ANN001
    lines = [
        f"## Milestone `{ev.contract.milestone_id}` — {ev.contract.title}",
        "",
        f"**Claimed score:** {ev.score:.1f} / 100  ·  **Verdict:** `{ev.verdict}`",
        "",
        "| Check | Target | Result | Detail |",
        "|---|---|---|---|",
    ]
    for r in [*ev.artifact_results, *ev.toolchain_results]:
        lines.append(f"| `{r.id}` | `{r.target}` | {'PASS' if r.passed else 'FAIL'} | {r.detail} |")
    if ev.integrity_flags:
        lines += ["", "### Integrity signals raised", ""]
        lines += [f"- `{r.id}`: {r.detail}" for r in ev.integrity_flags]
        lines += ["", "_Signals are advisory. An instructor reviews them; they do not fail you._"]
    if ev.contract.defense_required:
        lines += [
            "",
            "> Automated validation never yields COMPLETE. Book your defense session "
            f"(min score {ev.contract.defense_min_score:.0f}) to close this milestone.",
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="academy-validate")
    ap.add_argument("--contract", required=True, type=Path)
    ap.add_argument("--schema", required=True, type=Path)
    ap.add_argument("--repo-root", default=Path("."), type=Path)
    ap.add_argument("--attestation", type=Path)
    ap.add_argument("--github-summary", type=Path)
    ap.add_argument("--fail-on", default="FAIL", choices=["FAIL", "NEEDS_REVISION", "NEVER"])
    a = ap.parse_args(argv)

    contract = Contract.load(a.contract, a.schema)
    ev = evaluate(contract, a.repo_root.resolve())
    att = ev.to_attestation()

    if a.attestation:
        a.attestation.parent.mkdir(parents=True, exist_ok=True)
        a.attestation.write_text(json.dumps(att, indent=2), encoding="utf-8")

    summary = _summary(ev)
    print(summary)
    if a.github_summary:
        with a.github_summary.open("a", encoding="utf-8") as fh:
            fh.write(summary + "\n")

    import os
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"score={ev.score:.2f}\nverdict={ev.verdict}\n")

    if a.fail_on == "NEVER":
        return 0
    order = {"PASS": 0, "NEEDS_REVISION": 1, "FAIL": 2}
    return 1 if order[ev.verdict] >= order[a.fail_on] else 0


if __name__ == "__main__":
    sys.exit(main())
