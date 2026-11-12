"""Synthetic normalized mappings; no Home Assistant instance or I/O."""

from copy import deepcopy
from decimal import Decimal
from types import MappingProxyType, SimpleNamespace

import pytest

from custom_components.helianthus.semantic_asset_preview import preview_semantic_assets
from custom_components.helianthus.semantic_snapshot_coordinator import semantic_asset_ids


def readable(asset_id):
    return {
        "asset_id": asset_id, "unavailable_reason": None,
        "snapshot": {"asset_id": asset_id, "facts": [
            {"value": {"boolean": False, "quantity": Decimal(0)}}]},
        "evaluation": {"facts": ()}, "projection": {"dispositions": ()},
        "selections": (),
    }


def unavailable(asset_id, reason):
    return {"asset_id": asset_id, "unavailable_reason": reason,
            "snapshot": None, "evaluation": None, "projection": None,
            "selections": ()}


def test_sparse_snapshot_preserves_every_selection_in_selected_order():
    selected = ["missing", "blocked", "readable"]
    source = {"assets": [readable("foreign"), readable("readable"), unavailable(
        "blocked", "semantic.read.reason.publication_withheld")]}
    before = deepcopy(source)
    assert preview_semantic_assets(source, selected) == [
        {"asset_id": "missing", "status": "selected-not-in-this-snapshot",
         "unavailable_reason": None, "asset": None},
        {"asset_id": "blocked", "status": "selected-unavailable",
         "unavailable_reason": "semantic.read.reason.publication_withheld",
         "asset": before["assets"][2]},
        {"asset_id": "readable", "status": "selected-readable",
         "unavailable_reason": None, "asset": before["assets"][1]},
    ]
    assert source == before
    assert selected == ["missing", "blocked", "readable"]


@pytest.mark.parametrize("suffix", ["asset_unknown", "snapshot_unavailable",
                                    "source_unavailable", "publication_withheld"])
def test_explicit_unavailable_reason_is_preserved_even_for_asset_unknown(suffix):
    reason = "semantic.read.reason." + suffix
    assert preview_semantic_assets({"assets": [unavailable("a", reason)]}, ["a"]) == [
        {"asset_id": "a", "status": "selected-unavailable",
         "unavailable_reason": reason, "asset": unavailable("a", reason)}]


@pytest.mark.parametrize("snapshot,status", [
    (None, "unknown"), ({"assets": ()}, "selected-not-in-this-snapshot"),
])
def test_missing_current_read_is_unknown_rather_than_absence(snapshot, status):
    selected = ["b", "a"]
    assert preview_semantic_assets(snapshot, selected) == [
        {"asset_id": "b", "status": status, "unavailable_reason": None, "asset": None},
        {"asset_id": "a", "status": status, "unavailable_reason": None, "asset": None},
    ]
    assert selected == ["b", "a"]


VALID = [[], [str(i) for i in range(32)], ["x" * 256], ["é" * 128],
         [" asset:A ", "asset:a", "asset:A", "a,b\nc", "e\u0301", "é", " "]]
INVALID = [None, "a", ("a",), {}, 7, True, [1], [False], [None], [[]], [{}],
           [""], ["x" * 257], ["é" * 128 + "a"], ["\ud800"], ["a", "a"],
           [str(i) for i in range(33)]]


@pytest.mark.parametrize("ids", VALID)
def test_valid_selection_limits_and_opaque_bytes_match_existing_helper(ids):
    assert semantic_asset_ids(SimpleNamespace(options={"semantic_asset_ids": ids}, data={})) == tuple(ids)
    rows = preview_semantic_assets(None, ids)
    assert [row["asset_id"].encode("utf-8") for row in rows] == [s.encode("utf-8") for s in ids]
    assert len(rows) == len(ids)


@pytest.mark.parametrize("ids", INVALID)
def test_invalid_selection_matches_existing_helper_without_mutation(ids):
    before = deepcopy(ids)
    with pytest.raises(ValueError):
        semantic_asset_ids(SimpleNamespace(options={"semantic_asset_ids": ids}, data={}))
    with pytest.raises(ValueError):
        preview_semantic_assets(None, ids)
    assert ids == before


@pytest.mark.parametrize("selected_id", ["asset:A", "asset:a", " asset:A ", "e\u0301", "é"])
def test_asset_id_is_the_only_lookup_key_and_is_never_coerced(selected_id):
    # Each variant kills matching by position, case folding, trimming or Unicode normalization.
    foreign = readable("foreign")
    target = readable(selected_id)
    rows = preview_semantic_assets({"assets": [foreign, target]}, [selected_id])
    assert rows == [{"asset_id": selected_id, "status": "selected-readable",
                     "unavailable_reason": None, "asset": target}]
    others = [name for name in ("asset:A", "asset:a", " asset:A ", "e\u0301", "é")
              if name != selected_id]
    assert all(row["status"] == "selected-not-in-this-snapshot" for row in
               preview_semantic_assets({"assets": [target]}, others))


def test_false_zero_and_empty_collections_are_readable_and_detached_both_ways():
    target = readable("a")
    view = MappingProxyType({"assets": (MappingProxyType(target),)})
    rows = preview_semantic_assets(view, ["a"])
    assert rows[0]["status"] == "selected-readable"
    assert rows[0]["asset"] == readable("a")
    target["snapshot"]["facts"][0]["value"]["boolean"] = True
    assert rows[0]["asset"]["snapshot"]["facts"][0]["value"]["boolean"] is False
    rows[0]["asset"]["snapshot"]["facts"][0]["value"]["quantity"] = Decimal(5)
    assert target["snapshot"]["facts"][0]["value"]["quantity"] == Decimal(0)


@pytest.mark.parametrize("foreign", [None, readable("foreign"), unavailable(
    "foreign", "semantic.read.reason.source_unavailable")])
def test_unrelated_asset_changes_cannot_change_selected_result(foreign):
    target = readable("a")
    source = {"assets": [target] if foreign is None else [foreign, target]}
    assert preview_semantic_assets(source, ["a"]) == [
        {"asset_id": "a", "status": "selected-readable",
         "unavailable_reason": None, "asset": readable("a")}]


def test_successive_previews_do_not_remove_or_reattach_saved_ids():
    selected = ["a", "b"]
    previous = preview_semantic_assets({"assets": [readable("a")]}, selected)
    assert preview_semantic_assets(None, selected) == [
        {"asset_id": "a", "status": "unknown", "unavailable_reason": None, "asset": None},
        {"asset_id": "b", "status": "unknown", "unavailable_reason": None, "asset": None}]
    assert previous[0]["asset"] == readable("a")
    assert previous[1]["asset"] is None
    assert selected == ["a", "b"]
