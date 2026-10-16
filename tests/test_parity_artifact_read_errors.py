"""Bounded loader and CLI failures for synthetic parity artifacts."""

from pathlib import Path
import runpy
import sys

import pytest

from custom_components.helianthus import parity_gate


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("missing", "parity artifact missing"),
        ("directory", "parity artifact could not be read"),
        ("permission", "parity artifact could not be read"),
        ("io", "parity artifact could not be read"),
        ("utf8", "parity artifact is not valid UTF-8"),
        ("json", "parity artifact is invalid json"),
        ("array", "parity artifact root must be a JSON object"),
        ("null", "parity artifact root must be a JSON object"),
        ("string", "parity artifact root must be a JSON object"),
    ],
)
def test_artifact_failures_are_bounded_in_loader_and_cli(
    tmp_path, monkeypatch, capsys, failure, message
) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "check_gateway_parity_gate.py"
    main = runpy.run_path(str(script))["main"]
    marker = "synthetic-credential-must-stay-private"
    artifact = tmp_path / f"{marker}.json"
    if failure == "directory":
        artifact.mkdir()
    elif failure in ("permission", "io"):
        error_type = PermissionError if failure == "permission" else OSError

        def fail_read(self, *args, **kwargs):
            raise error_type(marker)

        monkeypatch.setattr(Path, "read_text", fail_read)
    elif failure == "utf8":
        artifact.write_bytes(marker.encode() + b"\xff")
    elif failure == "json":
        artifact.write_text(f'{{"{marker}":', encoding="utf-8")
    elif failure in ("array", "null", "string"):
        roots = {"array": f'["{marker}"]', "null": "null", "string": f'"{marker}"'}
        artifact.write_text(roots[failure], encoding="utf-8")

    with pytest.raises(parity_gate.ParityGateValidationError) as caught:
        parity_gate.enforce_gateway_parity_gate(artifact)
    assert str(caught.value) == message

    monkeypatch.setattr(sys, "argv", [str(script), "--artifact", str(artifact)])
    assert main() == 1
    output = capsys.readouterr()
    assert output.out == f"Gateway parity gate: FAIL ({message})\n"
    assert output.err == ""
    assert marker not in output.out
