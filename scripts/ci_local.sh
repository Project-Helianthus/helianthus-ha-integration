#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

echo "==> terminology gate"
if git grep -nIwiE 'm[a]ster|s[l]ave'; then
  echo "Found legacy terminology."
  exit 1
fi

echo "==> python tests"
pytest

echo "==> gateway parity gate readiness"
python3 scripts/check_gateway_parity_gate.py --artifact tests/fixtures/gateway_parity_artifact_pass.json

echo "==> private IPv4 address gate (docs must use placeholders)"
python3 - <<'PY'
from __future__ import annotations

import ipaddress
import pathlib
import re
import subprocess
import sys

md_files = subprocess.check_output(["git", "ls-files", "*.md"], text=True).splitlines()
ipv4_re = re.compile(r"\\b(?:(?:\\d{1,3})\\.){3}(?:\\d{1,3})\\b")

private_nets = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
]

def is_leaked_private(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.version != 4:
        return False
    return any(addr in net for net in private_nets)

failed = False

for file_path in md_files:
    text = pathlib.Path(file_path).read_text(encoding="utf-8")
    for match in ipv4_re.finditer(text):
        ip = match.group(0)
        if not is_leaked_private(ip):
            continue
        line = text.count("\n", 0, match.start()) + 1
        print(f"{file_path}:{line}: private IPv4 address found (use a placeholder)", file=sys.stderr)
        failed = True

if failed:
    sys.exit(1)
print("Private IPv4 gate passed.")
PY
