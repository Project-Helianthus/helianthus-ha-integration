#!/usr/bin/env python3
"""Validate gateway parity gate artifact for HA integration rollout readiness."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from custom_components.helianthus.parity_gate import (
    ParityGateValidationError,
    enforce_gateway_parity_gate,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate gateway parity gate artifact")
    parser.add_argument(
        "--artifact",
        required=True,
        help="Path to gateway parity gate artifact JSON",
    )
    parser.add_argument(
        "--source-repo",
        default="Project-Helianthus/helianthus-ebusgateway",
        help="Expected gateway source repository",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        enforce_gateway_parity_gate(args.artifact, expected_source_repo=args.source_repo)
    except ParityGateValidationError as exc:
        print(f"Gateway parity gate: FAIL ({exc})")
        return 1

    print("Gateway parity gate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
