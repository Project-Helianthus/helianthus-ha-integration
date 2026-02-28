#!/usr/bin/env python3
"""Run HA post-parity adoption checks only when gateway parity gate is green."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from custom_components.helianthus import parity_gate

ADOPTED_CAPABILITY_TESTS = (
    "tests/test_coordinator.py",
    "tests/test_energy.py",
    "tests/test_smoke_profile.py",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run post-parity HA adoption checks")
    parser.add_argument(
        "--artifact",
        required=True,
        help="Path to gateway parity artifact JSON",
    )
    parser.add_argument(
        "--source-repo",
        default="Project-Helianthus/helianthus-ebusgateway",
        help="Expected gateway source repository",
    )
    parser.add_argument(
        "--tests",
        nargs="+",
        default=list(ADOPTED_CAPABILITY_TESTS),
        help="pytest targets for adopted capabilities",
    )
    return parser.parse_args()


def run_post_parity_checks(artifact: str, source_repo: str, tests: list[str]) -> int:
    try:
        parity_gate.enforce_gateway_parity_gate(artifact, expected_source_repo=source_repo)
    except parity_gate.ParityGateValidationError as exc:
        print(f"Post-parity adoption checks blocked: {exc}")
        return 1

    cmd = ["pytest", *tests]
    print(f"Running adopted capability checks: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(REPO_ROOT), check=False)
    return int(result.returncode)


def main() -> int:
    args = parse_args()
    return run_post_parity_checks(args.artifact, args.source_repo, args.tests)


if __name__ == "__main__":
    raise SystemExit(main())
