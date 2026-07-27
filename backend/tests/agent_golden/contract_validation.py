from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from agent_golden.support import (
    assert_contiguous_order,
    assert_monotonic_timestamps,
    load_json,
    resolve_json_pointer,
)

FIXTURE_SETS = {
    "legacy_characterization": "legacy",
    "contract_v1": "contract_v1",
}
PLANES = ("provider", "domain", "sse", "db_events", "conversation")
TERMINAL_SSE_EVENTS = {"complete", "stream_cancelled", "stream_interrupted"}


@dataclass(frozen=True)
class ExpectedFixture:
    path: Path
    fixture_set: str
    scenario_id: str
    variant_id: str
    plane: str
    applicability: str


def schema_registry(
    contract_root: Path,
    manifest: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], Registry]:
    schemas = {item["id"]: load_json(contract_root / item["path"]) for item in manifest["schemas"]}
    registry = Registry().with_resources(
        (schema_id, Resource.from_contents(schema)) for schema_id, schema in schemas.items()
    )
    return schemas, registry


def expected_fixture_matrix(contract_root: Path) -> dict[Path, ExpectedFixture]:
    catalog = load_json(contract_root / "scenario-catalog.json")
    expected: dict[Path, ExpectedFixture] = {}
    for scenario in catalog["scenarios"]:
        for variant in scenario["variants"]:
            for fixture_set, catalog_key in FIXTURE_SETS.items():
                fixture_root = catalog["fixture_roots"][catalog_key]
                for plane in PLANES:
                    relative = catalog["fixture_path_template"].format(
                        fixture_root=fixture_root,
                        scenario_id=scenario["id"],
                        fixture_key=variant["fixture_key"],
                        plane=plane,
                    )
                    path = contract_root / relative
                    assert path not in expected, f"duplicate fixture path: {path}"
                    expected[path] = ExpectedFixture(
                        path=path,
                        fixture_set=fixture_set,
                        scenario_id=scenario["id"],
                        variant_id=variant["variant_id"],
                        plane=plane,
                        applicability=variant["planes"][plane][catalog_key],
                    )
    return expected


def validate_fixture(
    contract_root: Path,
    expected: ExpectedFixture,
    schemas: dict[str, dict[str, Any]],
    registry: Registry,
) -> dict[str, Any]:
    envelope = load_json(expected.path)
    _validate(
        envelope,
        schemas["fixture-envelope.schema.json"],
        registry,
        expected.path,
    )
    assert envelope["fixture_set"] == expected.fixture_set
    assert envelope["scenario_id"] == expected.scenario_id
    assert envelope["variant_id"] == expected.variant_id
    assert envelope["plane"] == expected.plane
    assert envelope["applicability"] == expected.applicability

    if expected.applicability == "required":
        payload_schema_id = envelope["payload_schema"]
        assert payload_schema_id in schemas, f"unknown payload schema: {payload_schema_id}"
        _validate(envelope["payload"], schemas[payload_schema_id], registry, expected.path)
        assert envelope["content_hash"] == _payload_hash(envelope["payload"])
        _assert_plane_semantics(expected, envelope["payload"])
    else:
        assert envelope["payload_schema"] is None
        assert envelope["content_hash"] is None
        assert "payload" not in envelope

    observed_document = envelope.get("payload")
    for field, status in envelope["legacy_field_observation"].items():
        actual = "present" if _contains_key(observed_document, field) else "absent"
        assert status == actual, f"legacy field observation mismatch for {field!r}"

    source = envelope["source"]
    source_paths = [
        contract_root.parents[2] / item["path"] for item in source["artifacts"]
    ]
    assert all(path.is_file() for path in source_paths), source_paths
    for artifact, path in zip(source["artifacts"], source_paths, strict=True):
        assert artifact["sha256"] == f"sha256:{_file_hash(path)}", artifact["path"]
    return envelope


def validate_fixture_ownership(
    envelope: dict[str, Any], ownership: dict[str, Any]
) -> None:
    plane = envelope["plane"]
    rules = ownership["planes"][plane]
    if envelope["applicability"] == "required":
        payload = envelope["payload"]
        for owned in rules["owns"]:
            missing_paths = [
                path
                for path in owned["payload_paths"]
                if not _resolve_pointer_pattern(payload, path)
            ]
            if missing_paths:
                raise AssertionError(
                    f"owned claim {plane}.{owned['claim']} has unresolved payload paths: "
                    f"{missing_paths}"
                )
        forbidden_hits = _find_forbidden_keys(payload, set(rules["forbidden"]))
        if forbidden_hits:
            raise AssertionError(
                f"forbidden fields found in {plane} payload: {forbidden_hits}"
            )

    if envelope["fixture_set"] == "legacy_characterization":
        expectations = ownership["legacy_field_observation"]["expectations"]
        expected = {
            field: rule["overrides"].get(plane, rule["default"])
            for field, rule in expectations.items()
        }
        if envelope["legacy_field_observation"] != expected:
            raise AssertionError(
                f"legacy field observations differ from manifest for {plane}: "
                f"{envelope['legacy_field_observation']!r} != {expected!r}"
            )


def validate_fixture_relationships(
    envelopes: dict[str, dict[str, Any]],
    ownership: dict[str, Any],
    requirements: dict[str, Any] | None = None,
) -> None:
    if set(envelopes) != set(PLANES):
        raise AssertionError(f"fixture relationship group must contain all planes: {sorted(envelopes)}")
    identities = {
        (item["fixture_set"], item["scenario_id"], item["variant_id"])
        for item in envelopes.values()
    }
    if len(identities) != 1:
        raise AssertionError(f"fixture relationship identity mismatch: {sorted(identities)}")
    fixture_set, _scenario_id, _variant_id = next(iter(identities))
    documents = {
        plane: envelope["payload"]
        for plane, envelope in envelopes.items()
        if envelope["applicability"] == "required"
    }
    join_rules = _rules_by_id(ownership["join_rules"], "join")
    order_rules = _rules_by_id(ownership["happens_before_rules"], "happens-before")
    seen: set[tuple[str, str]] = set()

    for envelope in envelopes.values():
        for relation in envelope["joins"]:
            identity = ("join", relation["name"])
            if identity in seen:
                raise AssertionError(f"duplicate fixture relationship name: {relation['name']!r}")
            seen.add(identity)
            rule = _relationship_rule(join_rules, relation["rule_id"], fixture_set)
            references = relation["references"]
            reference_keys = [
                (reference["plane"], reference["pointer"]) for reference in references
            ]
            if len(reference_keys) != len(set(reference_keys)):
                raise AssertionError(
                    f"join {relation['name']!r} contains duplicate references"
                )
            roles = [reference["role"] for reference in references]
            if len(roles) != len(set(roles)):
                raise AssertionError(
                    f"join {relation['name']!r} contains duplicate reference roles"
                )
            if len(references) < rule["minimum_references"]:
                raise AssertionError(
                    f"join {relation['name']!r} requires {rule['minimum_references']} references"
                )
            values = [
                _resolve_scoped_reference(documents, reference, rule["allowed_references"])
                for reference in references
            ]
            if any(value != values[0] for value in values[1:]):
                raise AssertionError(f"join {relation['name']!r} mismatch: {values!r}")

        for relation in envelope["happens_before"]:
            identity = ("happens_before", relation["name"])
            if identity in seen:
                raise AssertionError(f"duplicate fixture relationship name: {relation['name']!r}")
            seen.add(identity)
            rule = _relationship_rule(order_rules, relation["rule_id"], fixture_set)
            order_type = relation["order_type"]
            if order_type not in rule["order_types"]:
                raise AssertionError(
                    f"happens-before {relation['name']!r} disallows order type {order_type!r}"
                )
            before = _resolve_scoped_reference(
                documents, relation["before"], rule["before_references"]
            )
            after = _resolve_scoped_reference(
                documents, relation["after"], rule["after_references"]
            )
            if relation["before"] == relation["after"]:
                raise AssertionError(
                    f"happens-before {relation['name']!r} self-references one value"
                )
            if not _ordered_value(before, order_type) < _ordered_value(after, order_type):
                raise AssertionError(
                    f"happens-before {relation['name']!r} violated: {before!r} !< {after!r}"
                )

    if requirements is not None:
        _assert_required_relationships(envelopes, requirements)


def _assert_required_relationships(
    envelopes: dict[str, dict[str, Any]],
    requirements: dict[str, Any],
) -> None:
    fixture_set = next(iter(envelopes.values()))["fixture_set"]
    variant_id = next(iter(envelopes.values()))["variant_id"]
    documents = {
        plane: envelope["payload"]
        for plane, envelope in envelopes.items()
        if envelope["applicability"] == "required"
    }
    matches = [
        item
        for item in requirements["variants"]
        if item["fixture_set"] == fixture_set and item["variant_id"] == variant_id
    ]
    if not matches:
        raise AssertionError(
            f"missing relationship requirements for {fixture_set}/{variant_id}"
        )
    if len(matches) != 1:
        raise AssertionError(
            f"duplicate relationship requirements for {fixture_set}/{variant_id}"
        )
    profiles = {
        item["id"]: item for item in requirements["reference_profiles"]
    }
    if len(profiles) != len(requirements["reference_profiles"]):
        raise AssertionError("duplicate relationship reference profile id")
    actual = {
        ("join" if kind == "join" else "happens_before", plane, relation["name"]): relation
        for plane, envelope in envelopes.items()
        for kind, collection in (
            ("join", envelope["joins"]),
            ("happens_before", envelope["happens_before"]),
        )
        for relation in collection
    }
    required = {
        (item["kind"], item["plane"], item["name"]): item
        for item in matches[0]["relations"]
    }
    if len(required) != len(matches[0]["relations"]):
        raise AssertionError(
            f"duplicate relationship requirements for {fixture_set}/{variant_id}"
        )
    missing = set(required) - set(actual)
    if missing:
        raise AssertionError(f"missing required fixture relationships: {sorted(missing)}")
    for identity, requirement in required.items():
        profile = profiles.get(requirement["profile_id"])
        if profile is None:
            raise AssertionError(
                f"unknown relationship reference profile: {requirement['profile_id']!r}"
            )
        if profile["kind"] != requirement["kind"]:
            raise AssertionError(
                f"relationship profile kind mismatch for {identity}: {profile['kind']!r}"
            )
        relation = actual[identity]
        if relation["rule_id"] != profile["rule_id"]:
            raise AssertionError(
                f"relationship rule mismatch for {identity}: "
                f"{relation['rule_id']!r} != {profile['rule_id']!r}"
            )
        if requirement["kind"] == "join":
            _assert_join_profile(identity, relation, profile, documents)
        else:
            _assert_order_profile(identity, relation, profile, documents)


def _assert_join_profile(
    identity: tuple[str, str, str],
    relation: dict[str, Any],
    profile: dict[str, Any],
    documents: dict[str, Any],
) -> None:
    expected = {item["role"]: item for item in profile["references"]}
    actual = {item["role"]: item for item in relation["references"]}
    if len(expected) != len(profile["references"]):
        raise AssertionError(f"duplicate roles in relationship profile {profile['id']!r}")
    if set(actual) != set(expected):
        raise AssertionError(
            f"relationship reference roles mismatch for {identity}: "
            f"{sorted(actual)} != {sorted(expected)}"
        )
    for role, endpoint in actual.items():
        _assert_endpoint_pattern(identity, endpoint, expected[role], documents)


def _assert_order_profile(
    identity: tuple[str, str, str],
    relation: dict[str, Any],
    profile: dict[str, Any],
    documents: dict[str, Any],
) -> None:
    if relation["order_type"] != profile["order_type"]:
        raise AssertionError(
            f"relationship order type mismatch for {identity}: "
            f"{relation['order_type']!r} != {profile['order_type']!r}"
        )
    _assert_endpoint_pattern(identity, relation["before"], profile["before"], documents)
    _assert_endpoint_pattern(identity, relation["after"], profile["after"], documents)


def _assert_endpoint_pattern(
    identity: tuple[str, str, str],
    endpoint: dict[str, str],
    expected: dict[str, Any],
    documents: dict[str, Any],
) -> None:
    if endpoint["role"] != expected["role"] or endpoint["plane"] != expected["plane"]:
        raise AssertionError(
            f"relationship endpoint role/plane mismatch for {identity}: "
            f"{endpoint!r} does not match {expected!r}"
        )
    if not _pointer_matches_pattern(endpoint["pointer"], expected["pointer_pattern"]):
        raise AssertionError(
            f"relationship endpoint path mismatch for {identity}: "
            f"{endpoint['pointer']!r} does not match {expected['pointer_pattern']!r}"
        )
    qualifier = expected.get("qualifier")
    if qualifier is not None:
        pointer_parts = endpoint["pointer"].split("/")[1:]
        levels_up = qualifier["levels_up"]
        if levels_up > len(pointer_parts):
            raise AssertionError(
                f"relationship qualifier escapes endpoint root for {identity}"
            )
        container_parts = pointer_parts[:-levels_up]
        container_pointer = "/" + "/".join(container_parts) if container_parts else ""
        container = resolve_json_pointer(
            documents[endpoint["plane"]], container_pointer
        )
        actual_qualifier = resolve_json_pointer(
            container, qualifier["relative_pointer"]
        )
        if actual_qualifier != qualifier["equals"]:
            raise AssertionError(
                f"relationship endpoint qualifier mismatch for {identity}: "
                f"{actual_qualifier!r} != {qualifier['equals']!r}"
            )


def _rules_by_id(rules: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    result = {rule["id"]: rule for rule in rules}
    if len(result) != len(rules):
        raise AssertionError(f"duplicate {label} rule id")
    return result


def _relationship_rule(
    rules: dict[str, dict[str, Any]], rule_id: str, fixture_set: str
) -> dict[str, Any]:
    rule = rules.get(rule_id)
    if rule is None:
        raise AssertionError(f"unknown fixture relationship rule: {rule_id!r}")
    if fixture_set not in rule["fixture_sets"]:
        raise AssertionError(f"rule {rule_id!r} does not allow fixture set {fixture_set!r}")
    return rule


def _resolve_scoped_reference(
    documents: dict[str, Any],
    reference: dict[str, str],
    scopes: list[dict[str, str]],
) -> Any:
    plane = reference["plane"]
    pointer = reference["pointer"]
    allowed = any(
        scope["plane"] == plane and _pointer_has_prefix(pointer, scope["pointer_prefix"])
        for scope in scopes
    )
    if not allowed:
        raise AssertionError(f"reference is outside rule scope: {plane}{pointer}")
    if plane not in documents:
        raise AssertionError(f"reference targets an omitted plane: {plane!r}")
    return resolve_json_pointer(documents[plane], pointer)


def _pointer_has_prefix(pointer: str, prefix: str) -> bool:
    return not prefix or pointer == prefix or pointer.startswith(f"{prefix}/")


def _pointer_matches_pattern(pointer: str, pattern: str) -> bool:
    pointer_parts = pointer.split("/")[1:] if pointer else []
    pattern_parts = pattern.split("/")[1:] if pattern else []
    return len(pointer_parts) == len(pattern_parts) and all(
        expected == "*" or actual == expected
        for actual, expected in zip(pointer_parts, pattern_parts, strict=True)
    )


def _resolve_pointer_pattern(document: Any, pattern: str) -> list[Any]:
    segments = pattern.split("/")[1:] if pattern else []
    values = [document]
    for encoded_segment in segments:
        segment = encoded_segment.replace("~1", "/").replace("~0", "~")
        matches: list[Any] = []
        for value in values:
            if segment == "*":
                if isinstance(value, dict):
                    matches.extend(value.values())
                elif isinstance(value, list):
                    matches.extend(value)
                continue
            if isinstance(value, dict) and segment in value:
                matches.append(value[segment])
                continue
            if isinstance(value, list) and segment.isdigit():
                index = int(segment)
                if index < len(value):
                    matches.append(value[index])
        values = matches
        if not values:
            break
    return values


def _find_forbidden_keys(
    value: Any,
    forbidden: set[str],
    path: str = "",
) -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            item_path = f"{path}/{key}"
            if key in forbidden:
                hits.append(item_path)
            hits.extend(_find_forbidden_keys(item, forbidden, item_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hits.extend(_find_forbidden_keys(item, forbidden, f"{path}/{index}"))
    return hits


def _ordered_value(value: Any, order_type: str) -> int | float | datetime:
    if order_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise AssertionError(f"integer order value required: {value!r}")
        return value
    if order_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AssertionError(f"numeric order value required: {value!r}")
        return value
    if not isinstance(value, str):
        raise TypeError(f"RFC3339 order value required: {value!r}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise AssertionError(f"invalid RFC3339 order value: {value!r}") from exc
    if parsed.tzinfo is None:
        raise AssertionError(f"RFC3339 order value must include timezone: {value!r}")
    return parsed


def conformance_gate_failures(report: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if report["legacy_characterization_status"] != "complete":
        failures.append("legacy_characterization")
    for field in (
        "schema_validation_results",
        "scenario_plane_results",
        "join_invariant_results",
        "happens_before_results",
        "http_sse_harness_results",
        "frontend_baseline_results",
    ):
        gate_items = [
            item
            for item in report[field]
            if item["gate_required"] and item["status"] != "future_phase"
        ]
        if not gate_items or any(
            item["status"] != "pass" or item["evidence_mode"] != "automated"
            for item in gate_items
        ):
            failures.append(field)
    for field in (
        "requirement_coverage",
        "vocabulary_coverage",
        "pairwise_coverage",
        "seam_coverage",
        "corpus_coverage",
    ):
        coverage = report[field]
        if (
            coverage["status"] != "complete"
            or coverage["covered"] != coverage["required"]
            or coverage["missing"]
        ):
            failures.append(field)
    if len(report["determinism_runs"]) < 2 or any(
        not item["canonical_match"] for item in report["determinism_runs"]
    ):
        failures.append("determinism_runs")
    if report["missing_assets"]:
        failures.append("missing_assets")
    reviews = report["reviews"]
    if not reviews or any(item.get("runtime_gate") != "go" for item in reviews):
        failures.append("reviews")
    expected_decision = "no_go" if failures else "go"
    if report["gate_decision"] != expected_decision:
        failures.append("gate_decision")
    if report["gate_decision"] == "go" and report["gate_reasons"]:
        failures.append("gate_reasons")
    if report["gate_decision"] == "no_go" and not report["gate_reasons"]:
        failures.append("gate_reasons")
    return failures


def manifest_closure_hash(contract_root: Path) -> str:
    manifest = load_json(contract_root / "manifest.json")
    corpus = load_json(contract_root / "legacy-skill-corpus.json")
    paths = (
        {item["path"] for item in manifest["schemas"]}
        | {
            item["path"]
            for item in manifest["instances"]
            if item["path"] != "conformance-report-phase0.json"
        }
        | {
            str(Path(item["fixture_path"]).relative_to("contracts/agent/v1"))
            for item in corpus["entries"]
        }
    )
    fixture_paths = {
        path.relative_to(contract_root).as_posix()
        for path in (contract_root / "fixtures").rglob("*.json")
    }
    paths |= fixture_paths
    entries = {path: f"sha256:{_file_hash(contract_root / path)}" for path in sorted(paths)}
    source_paths = {
        artifact["path"]
        for fixture_path in fixture_paths
        for artifact in load_json(contract_root / fixture_path)["source"]["artifacts"]
    }
    repo_root = contract_root.parents[2]
    entries.update(
        {
            f"repo://{path}": f"sha256:{_file_hash(repo_root / path)}"
            for path in sorted(source_paths)
        }
    )
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _validate(
    instance: Any,
    schema: dict[str, Any],
    registry: Registry,
    path: Path,
) -> None:
    validator = Draft202012Validator(
        schema,
        registry=registry,
        format_checker=FormatChecker(),
    )
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.absolute_path))
    assert not errors, "\n".join(
        f"{path}:{'/'.join(map(str, error.absolute_path))}: {error.message}" for error in errors
    )


def _contains_key(value: Any, field: str) -> bool:
    if isinstance(value, dict):
        return field in value or any(_contains_key(item, field) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, field) for item in value)
    return False


def _assert_plane_semantics(expected: ExpectedFixture, payload: dict[str, Any]) -> None:
    plane = expected.plane
    if plane == "provider":
        assert_contiguous_order(payload["exchanges"], "sequence")
        return
    if plane == "sse":
        events = payload["events"]
        assert_contiguous_order(events, "sequence")
        durable_ids = [item["id"] for item in events if item["id"] is not None]
        assert len(durable_ids) == len(set(durable_ids)), "duplicate durable SSE event id"
        if (
            expected.fixture_set == "legacy_characterization"
            and expected.variant_id == "GT13-llm-error"
        ):
            names = [item["event"] for item in events]
            assert names.count("error_occurred") == 1
            assert "complete" not in names
            assert names.index("error_occurred") < names.index("stream_end")
            assert names[-2:] == ["assistant_message_created", "session_state_changed"]
            return
        terminals = [
            index for index, item in enumerate(events) if item["event"] in TERMINAL_SSE_EVENTS
        ]
        assert len(terminals) == 1, f"expected one terminal SSE event: {terminals}"
        assert terminals[0] == len(events) - 1, "terminal SSE event must be last"
        return
    if plane == "db_events":
        events = payload["events"]
        assert_contiguous_order(events, "observed_row_order")
        event_ids = [item["event_id"] for item in events]
        assert len(event_ids) == len(set(event_ids)), "duplicate DB event id"
        assert_monotonic_timestamps(events, "created_at")


def _payload_hash(payload: Any) -> str:
    canonical = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
