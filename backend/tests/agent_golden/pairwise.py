from __future__ import annotations

import hashlib
import itertools
import json
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any


class PairwiseValidationError(ValueError):
    pass


Assignment = tuple[str, ...]
Pair = tuple[str, str, str, str]


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise PairwiseValidationError(f"expected object at {path}")
    return value


def profile_dimensions(
    manifest: Mapping[str, Any], profile_name: str
) -> dict[str, tuple[str, ...]]:
    profile = _profile(manifest, profile_name)
    dimensions = {}
    for name in manifest["generator"]["dimension_order"]:
        values = (
            profile["provider_boundary"]
            if name == "provider_boundary"
            else manifest["common_dimensions"][name]
        )
        dimensions[name] = tuple(values)
    return dimensions


def enumerate_legal_assignments(
    manifest: Mapping[str, Any], profile_name: str = "0A_legacy"
) -> tuple[Assignment, ...]:
    dimensions = profile_dimensions(manifest, profile_name)
    constraints = _validated_constraints(manifest, profile_name, dimensions)
    assignments = itertools.product(*(dimensions[name] for name in dimensions))
    legal = [
        assignment
        for assignment in assignments
        if _matching_constraints(assignment, dimensions, constraints)
    ]
    return tuple(sorted(legal))


def legal_pairs(manifest: Mapping[str, Any], profile_name: str = "0A_legacy") -> frozenset[Pair]:
    dimensions = profile_dimensions(manifest, profile_name)
    return frozenset(
        pair
        for assignment in enumerate_legal_assignments(manifest, profile_name)
        for pair in assignment_pairs(assignment, tuple(dimensions))
    )


def assignment_pairs(assignment: Assignment, dimension_names: Sequence[str]) -> frozenset[Pair]:
    return frozenset(
        (dimension_names[left], assignment[left], dimension_names[right], assignment[right])
        for left, right in itertools.combinations(range(len(dimension_names)), 2)
    )


def coverage_digest(pairs: Iterable[Pair]) -> str:
    canonical = json.dumps(sorted(pairs), separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def generate_cases(
    manifest: Mapping[str, Any], profile_name: str = "0A_legacy"
) -> list[dict[str, Any]]:
    dimensions = profile_dimensions(manifest, profile_name)
    dimension_names = tuple(dimensions)
    constraints = _validated_constraints(manifest, profile_name, dimensions)
    legal = enumerate_legal_assignments(manifest, profile_name)
    if not legal:
        raise PairwiseValidationError(f"profile {profile_name!r} has no legal assignments")

    pairs_by_assignment = {
        assignment: assignment_pairs(assignment, dimension_names) for assignment in legal
    }
    uncovered = set().union(*pairs_by_assignment.values())
    selected: list[Assignment] = []
    remaining = set(legal)

    while uncovered:
        assignment = min(
            remaining,
            key=lambda item: (-len(pairs_by_assignment[item] & uncovered), item),
        )
        newly_covered = pairs_by_assignment[assignment] & uncovered
        if not newly_covered:
            raise PairwiseValidationError("generator stalled with uncovered legal pairs")
        selected.append(assignment)
        remaining.remove(assignment)
        uncovered.difference_update(newly_covered)

    selected_set = set(selected)
    for constraint in constraints:
        candidates = [
            assignment
            for assignment in legal
            if _constraint_matches(assignment, dimensions, constraint)
        ]
        if not any(assignment in selected_set for assignment in candidates):
            assignment = min(candidates)
            selected.append(assignment)
            selected_set.add(assignment)

    cases: list[dict[str, Any]] = []
    for index, assignment in enumerate(selected, start=1):
        matching = _matching_constraints(assignment, dimensions, constraints)
        if len(matching) != 1:
            raise PairwiseValidationError(
                f"legal assignment must match exactly one constraint: {assignment!r}"
            )
        constraint = matching[0]
        cases.append(
            {
                "case_id": f"PW0A-{index:03d}",
                "constraint_id": constraint["id"],
                "variant_id": constraint["variant_id"],
                "entrypoint": constraint["entrypoint"],
                "values": dict(zip(dimension_names, assignment, strict=True)),
            }
        )
    return cases


def validate_pairwise_manifest(
    manifest: Mapping[str, Any],
    catalog: Mapping[str, Any],
    profile_name: str = "0A_legacy",
) -> None:
    dimensions = profile_dimensions(manifest, profile_name)
    dimension_names = tuple(dimensions)
    constraints = _validated_constraints(manifest, profile_name, dimensions)
    profile = _profile(manifest, profile_name)
    catalog_variants = {
        variant["variant_id"]: variant
        for scenario in catalog["scenarios"]
        for variant in scenario["variants"]
    }

    _validate_constraint_catalog_links(constraints, catalog_variants)

    cases = profile["cases"]
    case_ids = [case.get("case_id") for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise PairwiseValidationError("duplicate case_id")

    assignments: list[Assignment] = []
    covered_constraints: set[str] = set()
    for case in cases:
        values = case.get("values")
        if not isinstance(values, Mapping) or set(values) != set(dimension_names):
            raise PairwiseValidationError(
                f"case {case.get('case_id')!r} must define every dimension exactly once"
            )
        for name, value in values.items():
            if value not in dimensions[name]:
                raise PairwiseValidationError(
                    f"case {case.get('case_id')!r} has invalid value {name}={value!r}"
                )
        assignment = tuple(values[name] for name in dimension_names)
        matching = _matching_constraints(assignment, dimensions, constraints)
        if len(matching) != 1:
            raise PairwiseValidationError(f"case {case.get('case_id')!r} violates constraints")
        constraint = matching[0]
        if case.get("constraint_id") != constraint["id"]:
            raise PairwiseValidationError(f"case {case.get('case_id')!r} constraint_id mismatch")
        if case.get("variant_id") not in catalog_variants:
            raise PairwiseValidationError(
                f"case {case.get('case_id')!r} references unknown variant"
            )
        if case.get("variant_id") != constraint["variant_id"]:
            raise PairwiseValidationError(
                f"case {case.get('case_id')!r} variant does not match its constraint"
            )
        variant = catalog_variants[case["variant_id"]]
        if case.get("entrypoint") != variant["entrypoint"]:
            raise PairwiseValidationError(f"case {case.get('case_id')!r} entrypoint mismatch")
        _validate_case_semantics(case, variant)
        assignments.append(assignment)
        covered_constraints.add(constraint["id"])

    if len(assignments) != len(set(assignments)):
        raise PairwiseValidationError("duplicate case values")

    if profile["coverage"]["require_each_constraint"]:
        missing_constraints = {constraint["id"] for constraint in constraints} - covered_constraints
        if missing_constraints:
            raise PairwiseValidationError(
                f"constraints without cases: {sorted(missing_constraints)!r}"
            )

    required_pairs = legal_pairs(manifest, profile_name)
    covered_pairs = frozenset(
        pair for assignment in assignments for pair in assignment_pairs(assignment, dimension_names)
    )
    missing_pairs = required_pairs - covered_pairs
    if missing_pairs:
        preview = sorted(missing_pairs)[:5]
        raise PairwiseValidationError(f"missing legal pairs ({len(missing_pairs)}): {preview!r}")

    coverage = profile["coverage"]
    expected_values = {
        "legal_assignment_count": len(enumerate_legal_assignments(manifest, profile_name)),
        "legal_pair_count": len(required_pairs),
        "covered_pair_count": len(covered_pairs),
        "coverage_digest": coverage_digest(required_pairs),
    }
    for field, expected in expected_values.items():
        if coverage.get(field) != expected:
            raise PairwiseValidationError(
                f"coverage {field} mismatch: expected {expected!r}, got {coverage.get(field)!r}"
            )

    generated = generate_cases(manifest, profile_name)
    if cases != generated:
        raise PairwiseValidationError("checked-in cases differ from deterministic generator output")


def mutated_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return deepcopy(manifest)


def _profile(manifest: Mapping[str, Any], profile_name: str) -> Mapping[str, Any]:
    try:
        profile = manifest["coverage_profiles"][profile_name]
    except (KeyError, TypeError) as exc:
        raise PairwiseValidationError(f"unknown profile {profile_name!r}") from exc
    if not isinstance(profile, Mapping):
        raise PairwiseValidationError(f"profile {profile_name!r} must be an object")
    return profile


def _validated_constraints(
    manifest: Mapping[str, Any],
    profile_name: str,
    dimensions: Mapping[str, tuple[str, ...]],
) -> tuple[Mapping[str, Any], ...]:
    constraints = tuple(_profile(manifest, profile_name)["constraints"])
    ids = [constraint.get("id") for constraint in constraints]
    if len(ids) != len(set(ids)):
        raise PairwiseValidationError("duplicate constraint id")
    for constraint in constraints:
        allowed = constraint.get("allowed")
        if not isinstance(allowed, Mapping) or set(allowed) != set(dimensions):
            raise PairwiseValidationError(
                f"constraint {constraint.get('id')!r} must define every dimension"
            )
        for name, values in allowed.items():
            if not values:
                raise PairwiseValidationError(
                    f"constraint {constraint.get('id')!r} has no values for {name}"
                )
            invalid = set(values) - set(dimensions[name])
            if invalid:
                raise PairwiseValidationError(
                    f"constraint {constraint.get('id')!r} has invalid {name} values: {sorted(invalid)!r}"
                )

    legal_assignments = itertools.product(*(dimensions[name] for name in dimensions))
    for assignment in legal_assignments:
        matching = _matching_constraints(assignment, dimensions, constraints)
        if len(matching) > 1:
            raise PairwiseValidationError(
                f"constraints overlap for assignment {assignment!r}: "
                f"{[item['id'] for item in matching]!r}"
            )
    return constraints


def _matching_constraints(
    assignment: Assignment,
    dimensions: Mapping[str, tuple[str, ...]],
    constraints: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        constraint
        for constraint in constraints
        if _constraint_matches(assignment, dimensions, constraint)
    )


def _constraint_matches(
    assignment: Assignment,
    dimensions: Mapping[str, tuple[str, ...]],
    constraint: Mapping[str, Any],
) -> bool:
    return all(
        value in constraint["allowed"][name]
        for name, value in zip(dimensions, assignment, strict=True)
    )


def _validate_constraint_catalog_links(
    constraints: Sequence[Mapping[str, Any]],
    catalog_variants: Mapping[str, Mapping[str, Any]],
) -> None:
    for constraint in constraints:
        variant_id = constraint.get("variant_id")
        if variant_id not in catalog_variants:
            raise PairwiseValidationError(
                f"constraint {constraint.get('id')!r} references unknown variant"
            )
        if constraint.get("entrypoint") != catalog_variants[variant_id]["entrypoint"]:
            raise PairwiseValidationError(
                f"constraint {constraint.get('id')!r} entrypoint mismatch"
            )


def _validate_case_semantics(case: Mapping[str, Any], variant: Mapping[str, Any]) -> None:
    values = case["values"]
    expected_entrypoint = "chat_sync" if values["transport"] == "sync" else "chat_sse"
    if case["entrypoint"] != expected_entrypoint:
        raise PairwiseValidationError(f"case {case.get('case_id')!r} transport/entrypoint mismatch")
    provider_applicability = variant["planes"]["provider"]["legacy"]
    expected_provider = (
        "not_applicable" if values["provider_boundary"] == "not_applicable" else "required"
    )
    if provider_applicability != expected_provider:
        raise PairwiseValidationError(
            f"case {case.get('case_id')!r} provider applicability mismatch"
        )
    allowed_terminals = {
        "success": {"completed"},
        "partial": {"waiting"},
        "failure": {"failed"},
        "timeout": {"waiting"},
        "cancel": {"cancelled"},
    }
    terminal = variant["contract_v1_expectation"]["terminal"]
    if terminal not in allowed_terminals[values["outcome"]]:
        raise PairwiseValidationError(f"case {case.get('case_id')!r} outcome/terminal mismatch")
    if values["session"] == "refresh" and not variant.get("refresh_action"):
        raise PairwiseValidationError(f"case {case.get('case_id')!r} refresh has no catalog action")
