# Integration release identity

The integration has its own semantic versions, independent of other Helianthus
components. The version in `custom_components/helianthus/manifest.json` is the
integration version. `0.0.0` is a development placeholder and is not a release.

Every user-visible change updates the `Unreleased` section of
[CHANGELOG.md](../CHANGELOG.md). At release, the owner assigns the actual
version, moves the applicable entries under exactly one matching version
heading, and creates an annotated `vMAJOR.MINOR.PATCH` tag. The tag without
`v`, the manifest version, and the changelog heading must match exactly. Keep
`Unreleased` for subsequent development. Do not infer the first integration
version from an umbrella release. The release workflow and tag step are an
owner handoff after review, tracked in a separate release-tracking issue until
the first release identity is assigned.

Prereleases use `vMAJOR.MINOR.PATCH-IDENTIFIERS`, where identifiers are
nonempty ASCII alphanumeric or hyphen strings separated by dots; numeric
identifiers have no leading zeroes. Core numeric components also have no
leading zeroes. Build metadata is not accepted. A prerelease tag, manifest
version, and changelog heading must match including the prerelease suffix.

Run `python3 tests/test_release_identity.py --check-tree` for the development
check. Before creating a release, run the same command with `--release-tag`
followed by the proposed tag; this rejects the placeholder. These checks do
not create tags or publish releases.
