"""Post the attestation as a GitHub Check Run so feedback lands in the PR diff."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import requests

API = "https://api.github.com"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="academy-publish-check")
    ap.add_argument("--attestation", required=True, type=Path)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--head-sha", required=True)
    ap.add_argument("--name", required=True)
    a = ap.parse_args(argv)

    att = json.loads(a.attestation.read_text(encoding="utf-8"))
    verdict = att["claimed_verdict"]
    conclusion = {"PASS": "success", "NEEDS_REVISION": "neutral", "FAIL": "failure"}[verdict]

    rows = [
        f"| `{r['id']}` | `{r['target']}` | {'PASS' if r['passed'] else 'FAIL'} | {r['detail']} |"
        for r in att["results"]["artifacts"] + att["results"]["toolchain"]
    ]
    body = (
        f"**Claimed score:** {att['claimed_score']} / 100\n\n"
        "| Check | Target | Result | Detail |\n|---|---|---|---|\n" + "\n".join(rows)
    )
    if att["requires_defense"]:
        body += "\n\n> Validation passing is necessary, not sufficient. Book your defense session."

    r = requests.post(
        f"{API}/repos/{a.repo}/check-runs",
        headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={
            "name": a.name,
            "head_sha": a.head_sha,
            "status": "completed",
            "conclusion": conclusion,
            "output": {
                "title": f"{verdict} — {att['claimed_score']}/100",
                "summary": body[:65000],
            },
        },
        timeout=30,
    )
    r.raise_for_status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
