"""Validate the integration's release identity without accessing Git."""

import argparse
import json
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORE = r"(?:0|[1-9][0-9]*)"
IDENTIFIER = r"(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
VERSION = re.compile(rf"{CORE}\.{CORE}\.{CORE}(?:-{IDENTIFIER}(?:\.{IDENTIFIER})*)?\Z")
HEADING = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def validate_identity(manifest, changelog, *, release_tag=None):
    """Return validation errors for supplied data; release mode requires a tag."""
    errors = []
    version = manifest.get("version") if isinstance(manifest, dict) else None
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        errors.append("invalid manifest version")
    headings = HEADING.findall(changelog)
    if headings.count("Unreleased") != 1:
        errors.append("expected exactly one Unreleased heading")
    if release_tag is not None:
        if not isinstance(release_tag, str) or not release_tag.startswith("v") or not VERSION.fullmatch(release_tag[1:]):
            errors.append("invalid release tag")
        elif release_tag[1:] != version:
            errors.append("tag and manifest differ")
        if version == "0.0.0":
            errors.append("placeholder is development only")
        if not isinstance(version, str) or headings.count(version) != 1:
            errors.append("expected exactly one matching version heading")
    return errors


@pytest.mark.parametrize("version,tag,headings,valid", [
    ("1.2.3", "v1.2.3", "## Unreleased\n## 1.2.3\n", True),
    ("1.2.3-rc.1", "v1.2.3-rc.1", "## Unreleased\n## 1.2.3-rc.1\n", True),
    ("1.2.3", "v1.2.4", "## Unreleased\n## 1.2.3\n", False),
    ("1.2.3", "v1.2.3", "## Unreleased\n", False),
    ("1.2.3", "v1.2.3", "## Unreleased\n## 1.2.3\n## 1.2.3\n", False),
    ("1.2.3", "1.2.3", "## Unreleased\n## 1.2.3\n", False),
    ("1.2.3", "v01.2.3", "## Unreleased\n## 1.2.3\n", False),
    ("1.2.3-rc.01", "v1.2.3-rc.01", "## Unreleased\n## 1.2.3-rc.01\n", False),
    ("1.2.3+build", "v1.2.3+build", "## Unreleased\n## 1.2.3+build\n", False),
    ("0.0.0", "v0.0.0", "## Unreleased\n## 0.0.0\n", False),
    ("1.2.3", "v1.2.3", "## 1.2.3\n", False),
    ("1.2.3", "v1.2.3", "## Unreleased\n## Unreleased\n## 1.2.3\n", False),
])
def test_release_identity(version, tag, headings, valid):
    assert (not validate_identity({"version": version}, headings, release_tag=tag)) is valid


def test_development_placeholder():
    assert not validate_identity({"version": "0.0.0"}, "## Unreleased\n")
    assert validate_identity({"version": "bad"}, "## Unreleased\n")


def test_repository_identity():
    manifest = json.loads((ROOT / "custom_components/helianthus/manifest.json").read_text())
    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert validate_identity(manifest, changelog) == []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-tree", action="store_true", required=True)
    parser.add_argument("--release-tag", help="validate a proposed release tag")
    args = parser.parse_args()
    try:
        manifest = json.loads((ROOT / "custom_components/helianthus/manifest.json").read_text())
        changelog = (ROOT / "CHANGELOG.md").read_text()
        errors = validate_identity(manifest, changelog, release_tag=args.release_tag)
    except (OSError, ValueError) as exc:
        errors = [str(exc)]
    for error in errors:
        print(error)
    if not errors:
        print("Release identity valid")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
