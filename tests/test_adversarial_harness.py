"""Offline adversarial-harness contract tests."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from scripts import ha_adversarial_harness as harness


FIXTURES = Path(__file__).parent / "fixtures/adversarial-runtime/v1/gateway"
TEST_COMMIT = "a" * 40


def _identity() -> str:
    return TEST_COMMIT


@pytest.mark.parametrize("case,verdict", [
    ("offline-all-pass", "pass"),
    ("evaluated-fail", "fail"),
    ("execution-error", "fail"),
    ("infrastructure-block", "blocked-infra"),
])
def test_four_canonical_gateway_reports_replay_and_preserve_verdict(
    tmp_path: Path, case: str, verdict: str
) -> None:
    output = tmp_path / f"{case}.json"
    actual = harness.run(FIXTURES / f"{case}.json", output, identity_provider=_identity)

    assert actual == verdict
    report = json.loads(output.read_text())
    assert report["summary"]["verdict"] == verdict
    assert report["provenance"]["producer"] == {
        "repository": harness.HA_REPOSITORY,
        "commit": TEST_COMMIT,
        "component": "ha-adversarial-harness",
        "build_kind": "ha-harness",
        "build_sha256": hashlib.sha256(Path(harness.__file__).read_bytes()).hexdigest(),
        "input_gateway_report_sha256": harness.EXPECTED_GATEWAY_SHA[case],
    }
    assert (output.stat().st_mode & 0o777) == 0o600


def test_replays_exercise_restart_two_gap_partition_and_delayed_reload() -> None:
    # The public helper is intentionally callable on its own so a test cannot
    # accidentally turn the harness into a report-copying formatter.
    for replay in (harness._replay_adv01, harness._replay_adv02, harness._replay_adv03, harness._replay_adv04):
        replay()


def test_replay_uses_real_zone_and_dhw_write_methods() -> None:
    harness._exercise_real_write_fences()


def test_replay_reads_real_zone_and_dhw_availability_properties(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness._install_entity_stubs()
    from custom_components.helianthus.climate import HelianthusZoneClimate
    from custom_components.helianthus.water_heater import HelianthusDhwWaterHeater

    reads = {"zone": 0, "dhw": 0}
    zone_available = HelianthusZoneClimate.available
    dhw_available = HelianthusDhwWaterHeater.available

    def read_zone(entity: object) -> bool:
        reads["zone"] += 1
        return zone_available.__get__(entity, type(entity))

    def read_dhw(entity: object) -> bool:
        reads["dhw"] += 1
        return dhw_available.__get__(entity, type(entity))

    monkeypatch.setattr(HelianthusZoneClimate, "available", property(read_zone))
    monkeypatch.setattr(HelianthusDhwWaterHeater, "available", property(read_dhw))
    harness._exercise_real_write_fences()
    assert reads["zone"] >= 4
    assert reads["dhw"] >= 4


def test_adv04_uses_production_delayed_inventory_listener_path() -> None:
    harness._replay_actual_delayed_inventory_listener()


@pytest.mark.parametrize("payload", [
    b"\xff",
    b'{"schema_version": 1, "schema_version": 1}',
    b'{"value": 1.5}',
    b'{"value": NaN}',
    b'[]',
])
def test_parser_rejects_utf8_duplicate_floats_nonfinite_and_nonobject(
    tmp_path: Path, payload: bytes
) -> None:
    source = tmp_path / "input.json"
    source.write_bytes(payload)
    with pytest.raises(harness.HarnessError):
        harness.load_gateway_report(source)


def test_unknown_extra_wrong_suite_order_and_provenance_fail_before_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = json.loads((FIXTURES / "offline-all-pass.json").read_text())
    variants = []
    unknown = json.loads(json.dumps(source))
    unknown["extra"] = True
    variants.append(unknown)
    suite = json.loads(json.dumps(source))
    suite["suite"]["version"] = 2
    variants.append(suite)
    schema = json.loads(json.dumps(source))
    schema["schema_version"] = 2
    variants.append(schema)
    fixture_case = json.loads(json.dumps(source))
    fixture_case["provenance"]["fixture_case_id"] = "wrong-case"
    variants.append(fixture_case)
    ordered = json.loads(json.dumps(source))
    ordered["scenarios"].reverse()
    variants.append(ordered)
    provenance = json.loads(json.dumps(source))
    provenance["provenance"]["subject"]["repository"] = "other/repository"
    variants.append(provenance)
    monkeypatch.setattr(harness, "replay_ha_contract", lambda _report: pytest.fail("probe ran"))
    for index, variant in enumerate(variants):
        path = tmp_path / f"variant-{index}.json"
        path.write_text(json.dumps(variant))
        with pytest.raises(harness.HarnessError):
            harness.run(path, tmp_path / f"out-{index}.json", identity_provider=_identity)


def test_one_byte_change_and_wrapper_input_are_rejected(tmp_path: Path) -> None:
    original = (FIXTURES / "offline-all-pass.json").read_bytes()
    mutated = bytearray(original)
    mutated[-2] = ord(" ")
    changed = tmp_path / "changed.json"
    changed.write_bytes(mutated)
    with pytest.raises(harness.HarnessError):
        harness.load_gateway_report(changed)

    wrapper = tmp_path / "wrapper.json"
    harness.run(FIXTURES / "offline-all-pass.json", wrapper, identity_provider=_identity)
    with pytest.raises(harness.HarnessError):
        harness.load_gateway_report(wrapper)


def test_path_size_and_symlink_fail_without_output(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * (harness.MAX_INPUT_BYTES + 1))
    with pytest.raises(harness.HarnessError):
        harness.load_gateway_report(oversized)
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(harness.HarnessError):
        harness.load_gateway_report(directory)
    fifo = tmp_path / "input.fifo"
    os.mkfifo(fifo)
    with pytest.raises(harness.HarnessError):
        harness.load_gateway_report(fifo)
    symlink = tmp_path / "link.json"
    symlink.symlink_to(FIXTURES / "offline-all-pass.json")
    with pytest.raises(harness.HarnessError):
        harness.load_gateway_report(symlink)


def test_regular_input_swapped_to_fifo_is_nonblocking_and_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "input.json"
    source.write_bytes((FIXTURES / "offline-all-pass.json").read_bytes())
    original_open = harness.os.open
    observed_flags: list[int] = []

    def swap_then_open(path: str | os.PathLike[str], flags: int, mode: int = 0o777) -> int:
        observed_flags.append(flags)
        if not flags & getattr(os, "O_NONBLOCK", 0):
            raise OSError("reader would block on swapped FIFO")
        source.unlink()
        os.mkfifo(source)
        return original_open(path, flags, mode)

    monkeypatch.setattr(harness.os, "open", swap_then_open)
    with pytest.raises(harness.HarnessError):
        harness.load_gateway_report(source)
    assert observed_flags[-1] & getattr(os, "O_NONBLOCK", 0)


def test_readme_documents_direct_descriptor_publication_contract() -> None:
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")
    assert "O_CREAT|O_EXCL|O_NOFOLLOW" in readme
    assert "only exit 0" in readme.lower()
    assert "temporary regular file" not in readme
    assert "atomically linked" not in readme


def test_output_symlink_and_replay_failure_leave_no_pass_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target.json"
    target.write_text("do not overwrite")
    output = tmp_path / "output.json"
    output.symlink_to(target)
    with pytest.raises(harness.HarnessError):
        harness.run(FIXTURES / "offline-all-pass.json", output, identity_provider=_identity)
    assert target.read_text() == "do not overwrite"

    failed_output = tmp_path / "failed.json"
    monkeypatch.setattr(harness, "replay_ha_contract", lambda _report: (_ for _ in ()).throw(harness.HarnessError("probe failed")))
    with pytest.raises(harness.HarnessError):
        harness.run(FIXTURES / "offline-all-pass.json", failed_output, identity_provider=_identity)
    assert not failed_output.exists()


@pytest.mark.parametrize("failure", ["input", "replay", "identity", "write"])
def test_existing_owned_output_is_refused_and_preserved_on_each_current_run_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    output = tmp_path / "owned-report.json"
    harness.run(FIXTURES / "offline-all-pass.json", output, identity_provider=_identity)
    original = output.read_bytes()
    if failure == "input":
        source = tmp_path / "invalid.json"
        source.write_text("{}")
        identity = _identity
    else:
        source = FIXTURES / "offline-all-pass.json"
        identity = _identity
    if failure == "replay":
        monkeypatch.setattr(harness, "replay_ha_contract", lambda _report: (_ for _ in ()).throw(harness.HarnessError("replay failed")))
    elif failure == "identity":
        identity = lambda: (_ for _ in ()).throw(harness.HarnessError("identity failed"))
    elif failure == "write":
        monkeypatch.setattr(harness, "_atomic_write", lambda *_args: (_ for _ in ()).throw(harness.HarnessError("write failed")))
    with pytest.raises(harness.HarnessError):
        harness.run(source, output, identity_provider=identity)
    assert output.read_bytes() == original


def test_unrelated_file_inserted_before_no_replace_publication_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "report.json"
    unrelated = tmp_path / "unrelated.json"
    unrelated.write_bytes(b"unrelated evidence")
    original_open = harness.os.open

    def insert_then_open(path: str | os.PathLike[str], flags: int, mode: int = 0o777) -> int:
        if Path(path) == output:
            descriptor = original_open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(descriptor, unrelated.read_bytes())
            finally:
                os.close(descriptor)
        return original_open(path, flags, mode)

    monkeypatch.setattr(harness.os, "open", insert_then_open)
    with pytest.raises(harness.HarnessError):
        harness.run(FIXTURES / "offline-all-pass.json", output, identity_provider=_identity)
    assert output.read_bytes() == b"unrelated evidence"


def test_direct_output_has_no_staging_substitution_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "report.json"
    unrelated = tmp_path / "unrelated.json"
    unrelated.write_bytes(b"unrelated evidence")
    monkeypatch.setattr(
        harness.tempfile,
        "mkstemp",
        lambda *_args, **_kwargs: pytest.fail("staging path must not be created"),
    )
    monkeypatch.setattr(
        harness.os,
        "link",
        lambda *_args, **_kwargs: pytest.fail("publication must not link a staging path"),
    )
    assert harness.run(FIXTURES / "offline-all-pass.json", output, identity_provider=_identity) == "pass"
    assert unrelated.read_bytes() == b"unrelated evidence"
    assert json.loads(output.read_text())["summary"]["verdict"] == "pass"


def test_output_inode_substitution_before_finalize_fails_without_unlinking_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "report.json"
    unrelated = tmp_path / "unrelated.json"
    unrelated.write_bytes(b"unrelated evidence")
    original_fsync = harness.os.fsync
    original_unlink = harness.os.unlink
    original_link = harness.os.link

    def substitute_then_sync(descriptor: int) -> None:
        original_unlink(output)
        original_link(unrelated, output)
        original_fsync(descriptor)

    monkeypatch.setattr(harness.os, "fsync", substitute_then_sync)
    with pytest.raises(harness.HarnessError):
        harness.run(FIXTURES / "offline-all-pass.json", output, identity_provider=_identity)
    assert output.read_bytes() == b"unrelated evidence"
    assert unrelated.read_bytes() == b"unrelated evidence"


def test_cli_output_io_failure_is_bounded_contract_error(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(harness, "_clean_identity", lambda _root: TEST_COMMIT)
    code = harness.main([
        "--input-gateway-report", str(FIXTURES / "offline-all-pass.json"),
        "--output", "/dev/null/ha-adversarial-report.json",
    ])
    captured = capsys.readouterr()
    assert code == 2
    assert "Traceback" not in captured.err


def test_cli_output_permission_failure_is_bounded_contract_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "report.json"
    original_open = harness.os.open
    monkeypatch.setattr(harness, "_clean_identity", lambda _root: TEST_COMMIT)

    def deny_output(path: str | os.PathLike[str], flags: int, mode: int = 0o777) -> int:
        if Path(path) == output:
            raise PermissionError("denied by hostile test")
        return original_open(path, flags, mode)

    monkeypatch.setattr(harness.os, "open", deny_output)
    code = harness.main([
        "--input-gateway-report", str(FIXTURES / "offline-all-pass.json"),
        "--output", str(output),
    ])
    captured = capsys.readouterr()
    assert code == 2
    assert "Traceback" not in captured.err
    assert not output.exists()


def test_cli_input_permission_failure_is_bounded_contract_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "report.json"
    source = FIXTURES / "offline-all-pass.json"
    original_open = harness.os.open
    monkeypatch.setattr(harness, "_clean_identity", lambda _root: TEST_COMMIT)

    def deny_input(path: str | os.PathLike[str], flags: int, mode: int = 0o777) -> int:
        if Path(path) == source:
            raise PermissionError("denied by hostile test")
        return original_open(path, flags, mode)

    monkeypatch.setattr(harness.os, "open", deny_input)
    code = harness.main([
        "--input-gateway-report", str(source),
        "--output", str(output),
    ])
    captured = capsys.readouterr()
    assert code == 2
    assert "Traceback" not in captured.err
    assert not output.exists()


@pytest.mark.parametrize("error_type", [TypeError, AttributeError])
def test_cli_production_replay_exception_is_bounded_contract_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
) -> None:
    output = tmp_path / "report.json"

    def broken_replay(_report: dict) -> None:
        raise error_type("hostile production seam")

    monkeypatch.setattr(harness, "replay_ha_contract", broken_replay)
    code = harness.main([
        "--input-gateway-report", str(FIXTURES / "offline-all-pass.json"),
        "--output", str(output),
    ])
    captured = capsys.readouterr()
    assert code == 2
    assert "Traceback" not in captured.err
    assert not output.exists()


def test_verify_fixtures_production_replay_exception_is_bounded_contract_error(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        harness,
        "replay_ha_contract",
        lambda _report: (_ for _ in ()).throw(TypeError("hostile production seam")),
    )
    assert harness.main(["--verify-fixtures"]) == 2
    assert "Traceback" not in capsys.readouterr().err


def test_cli_exit_codes_and_clean_identity_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_clean_identity = harness._clean_identity
    monkeypatch.setattr(harness, "_clean_identity", lambda _root: TEST_COMMIT)
    pass_output = tmp_path / "pass.json"
    assert harness.main(["--input-gateway-report", str(FIXTURES / "offline-all-pass.json"), "--output", str(pass_output)]) == 0
    nonpass_output = tmp_path / "nonpass.json"
    assert harness.main(["--input-gateway-report", str(FIXTURES / "evaluated-fail.json"), "--output", str(nonpass_output)]) == 1
    invalid_output = tmp_path / "invalid.json"
    assert harness.main(["--input-gateway-report", str(tmp_path / "missing.json"), "--output", str(invalid_output)]) == 2
    assert not invalid_output.exists()

    monkeypatch.setattr(harness, "_clean_identity", original_clean_identity)
    monkeypatch.setattr(harness.subprocess, "run", lambda *args, **kwargs: type("Result", (), {"stdout": " M dirty\n"})())
    with pytest.raises(harness.HarnessError, match="clean"):
        harness._clean_identity(tmp_path)
