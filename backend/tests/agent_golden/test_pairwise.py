from __future__ import annotations

from pathlib import Path

import pytest

from agent_golden.pairwise import (
    PairwiseValidationError,
    assignment_pairs,
    generate_cases,
    load_json,
    mutated_manifest,
    profile_dimensions,
    validate_pairwise_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_ROOT = REPO_ROOT / "contracts" / "agent" / "v1"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return load_json(CONTRACT_ROOT / "pairwise-manifest.json")


@pytest.fixture(scope="module")
def catalog() -> dict:
    return load_json(CONTRACT_ROOT / "scenario-catalog.json")


def test_checked_in_pairwise_cases_are_valid_and_deterministic(
    manifest: dict, catalog: dict
) -> None:
    validate_pairwise_manifest(manifest, catalog)
    assert manifest["coverage_profiles"]["0A_legacy"]["cases"] == generate_cases(manifest)


def test_dimension_object_order_does_not_change_generation(manifest: dict) -> None:
    reordered = mutated_manifest(manifest)
    reordered["common_dimensions"] = dict(reversed(tuple(reordered["common_dimensions"].items())))
    assert generate_cases(reordered) == generate_cases(manifest)


def test_removing_case_exposes_a_missing_legal_pair(manifest: dict, catalog: dict) -> None:
    dimensions = tuple(profile_dimensions(manifest, "0A_legacy"))
    cases = manifest["coverage_profiles"]["0A_legacy"]["cases"]
    removable_index = next(
        index
        for index, case in enumerate(cases)
        if assignment_pairs(tuple(case["values"][name] for name in dimensions), dimensions)
        - set().union(
            *(
                assignment_pairs(tuple(other["values"][name] for name in dimensions), dimensions)
                for other_index, other in enumerate(cases)
                if other_index != index
            )
        )
    )
    mutated = mutated_manifest(manifest)
    del mutated["coverage_profiles"]["0A_legacy"]["cases"][removable_index]
    with pytest.raises(PairwiseValidationError, match="missing legal pairs"):
        validate_pairwise_manifest(mutated, catalog)


def test_invalid_dimension_value_is_rejected(manifest: dict, catalog: dict) -> None:
    mutated = mutated_manifest(manifest)
    mutated["coverage_profiles"]["0A_legacy"]["cases"][0]["values"]["transport"] = "websocket"
    with pytest.raises(PairwiseValidationError, match="invalid value"):
        validate_pairwise_manifest(mutated, catalog)


def test_constraint_violating_case_is_rejected(manifest: dict, catalog: dict) -> None:
    mutated = mutated_manifest(manifest)
    case = mutated["coverage_profiles"]["0A_legacy"]["cases"][0]
    case["values"].update(
        {
            "outcome": "cancel",
            "action": "knowledge",
            "provider_boundary": "local_raw_exchange",
        }
    )
    with pytest.raises(PairwiseValidationError, match="violates constraints"):
        validate_pairwise_manifest(mutated, catalog)


def test_unknown_variant_is_rejected(manifest: dict, catalog: dict) -> None:
    mutated = mutated_manifest(manifest)
    mutated["coverage_profiles"]["0A_legacy"]["cases"][0]["variant_id"] = "GT99-missing"
    with pytest.raises(PairwiseValidationError, match="unknown variant"):
        validate_pairwise_manifest(mutated, catalog)


def test_entrypoint_mismatch_is_rejected(manifest: dict, catalog: dict) -> None:
    mutated = mutated_manifest(manifest)
    case = mutated["coverage_profiles"]["0A_legacy"]["cases"][0]
    case["entrypoint"] = "chat_sync" if case["entrypoint"] == "chat_sse" else "chat_sse"
    with pytest.raises(PairwiseValidationError, match="entrypoint mismatch"):
        validate_pairwise_manifest(mutated, catalog)


def test_duplicate_case_id_is_rejected(manifest: dict, catalog: dict) -> None:
    mutated = mutated_manifest(manifest)
    cases = mutated["coverage_profiles"]["0A_legacy"]["cases"]
    cases[1]["case_id"] = cases[0]["case_id"]
    with pytest.raises(PairwiseValidationError, match="duplicate case_id"):
        validate_pairwise_manifest(mutated, catalog)
