from __future__ import annotations

import hashlib
import importlib.util
from copy import deepcopy
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from agent_golden.contract_validation import (
    PLANES,
    conformance_gate_failures,
    expected_fixture_matrix,
    manifest_closure_hash,
    schema_registry,
    validate_fixture,
    validate_fixture_ownership,
    validate_fixture_relationships,
)
from agent_golden.support import load_json
from agent_golden.update_seed_corpus import check as check_seed_corpus

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_ROOT = REPO_ROOT / "contracts" / "agent" / "v1"


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    return load_json(CONTRACT_ROOT / "manifest.json")


@pytest.fixture(scope="module")
def schema_documents(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["id"]: load_json(CONTRACT_ROOT / item["path"]) for item in manifest["schemas"]}


def _registry(schemas: dict[str, dict[str, Any]]) -> Registry:
    return Registry().with_resources(
        (schema_id, Resource.from_contents(schema)) for schema_id, schema in schemas.items()
    )


def _load_source_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("legacy_agent_loop_completion", path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load corpus source module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _unique(values: list[str], label: str) -> set[str]:
    assert len(values) == len(set(values)), f"duplicate {label}: {values}"
    return set(values)


def test_every_schema_is_valid_draft_2020_12(
    schema_documents: dict[str, dict[str, Any]],
) -> None:
    for schema_id, schema in schema_documents.items():
        assert schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema"
        assert schema.get("$id") == schema_id
        Draft202012Validator.check_schema(schema)


def test_relationship_schemas_accept_multisegment_json_pointers(
    schema_documents: dict[str, dict[str, Any]],
) -> None:
    pointer_schema = schema_documents["fixture-envelope.schema.json"]["$defs"]["planePointer"]
    validator = Draft202012Validator(pointer_schema)
    assert not list(
        validator.iter_errors(
            {
                "role": "observed_event_order",
                "plane": "db_events",
                "pointer": "/events/7/observed_row_order",
            }
        )
    )


def test_every_registered_instance_validates(
    manifest: dict[str, Any],
    schema_documents: dict[str, dict[str, Any]],
) -> None:
    registry = _registry(schema_documents)
    for item in manifest["instances"]:
        validator = Draft202012Validator(
            schema_documents[item["schema"]],
            registry=registry,
            format_checker=FormatChecker(),
        )
        errors = sorted(
            validator.iter_errors(load_json(CONTRACT_ROOT / item["path"])),
            key=lambda error: list(error.absolute_path),
        )
        assert not errors, "\n".join(
            f"{item['path']}:{'/'.join(map(str, error.absolute_path))}: {error.message}"
            for error in errors
        )


def test_manifest_has_no_orphan_or_duplicate_schema_or_instance(
    manifest: dict[str, Any],
) -> None:
    schema_ids = [item["id"] for item in manifest["schemas"]]
    schema_paths = [item["path"] for item in manifest["schemas"]]
    instance_paths = [item["path"] for item in manifest["instances"]]
    _unique(schema_ids, "schema id")
    _unique(schema_paths, "schema path")
    _unique(instance_paths, "instance path")

    actual_schema_paths = {
        path.relative_to(CONTRACT_ROOT).as_posix()
        for path in (CONTRACT_ROOT / "schemas").rglob("*.json")
    }
    assert set(schema_paths) == actual_schema_paths

    actual_instance_paths = {path.name for path in CONTRACT_ROOT.glob("*.json")}
    assert set(instance_paths) == actual_instance_paths
    assert {item["schema"] for item in manifest["instances"]} <= set(schema_ids)


def test_catalog_ids_and_semantic_references_are_closed() -> None:
    catalog = load_json(CONTRACT_ROOT / "scenario-catalog.json")
    registry = load_json(CONTRACT_ROOT / "requirement-registry.json")
    vocabulary = load_json(CONTRACT_ROOT / "scenario-vocabulary.json")

    scenario_ids = _unique([item["id"] for item in catalog["scenarios"]], "scenario id")
    variants = [variant for scenario in catalog["scenarios"] for variant in scenario["variants"]]
    variant_ids = _unique([item["variant_id"] for item in variants], "variant id")
    fixture_keys = _unique([item["fixture_key"] for item in variants], "fixture key")
    assert scenario_ids == {f"GT{index:02d}" for index in range(1, 18)}
    assert len(variant_ids) == len(fixture_keys) == 27

    requirement_ids = _unique(
        [item["id"] for item in registry["requirements"]],
        "requirement id",
    )
    used_requirements = {
        requirement for variant in variants for requirement in variant["requirements"]
    }
    assert used_requirements == requirement_ids

    for term_kind, variant_field in (
        ("pre_states", "pre_state"),
        ("refresh_actions", "refresh_action"),
    ):
        declared = _unique([item["id"] for item in vocabulary[term_kind]], term_kind)
        used = {variant[variant_field] for variant in variants}
        assert used == declared

    declared_steps = _unique([item["id"] for item in vocabulary["steps"]], "steps")
    used_steps = {
        step
        for variant in variants
        for field in ("steps", "legacy_steps", "contract_v1_steps")
        for step in variant.get(field, [])
    }
    assert used_steps == declared_steps

    for variant in variants:
        assert set(variant["planes"]) == {
            "provider",
            "domain",
            "sse",
            "db_events",
            "conversation",
        }

    required_variants = {
        "GT01": {"GT01-sync", "GT01-sse", "GT01-feedback-refresh-toggle"},
        "GT02": {"GT02-ask-refresh-continue"},
        "GT03": {"GT03-true", "GT03-false"},
        "GT04": {"GT04-merge"},
        "GT05": {"GT05-pending", "GT05-multi-frame"},
        "GT06": {"GT06-retry-success", "GT06-retry-limit"},
        "GT07": {"GT07-citation-refresh"},
        "GT08": {"GT08-query-before-advance"},
        "GT09": {"GT09-read"},
        "GT10": {"GT10-replay", "GT10-unknown"},
        "GT11": {"GT11-standalone", "GT11-sop"},
        "GT12": {"GT12-disconnect", "GT12-cancel"},
        "GT13": {"GT13-llm-error"},
        "GT14": {"GT14-create", "GT14-reply-resume"},
        "GT15": {"GT15-full"},
        "GT16": {"GT16-history"},
        "GT17": {"GT17-channel", "GT17-scheduled"},
    }
    assert {
        scenario["id"]: {variant["variant_id"] for variant in scenario["variants"]}
        for scenario in catalog["scenarios"]
    } == required_variants


def test_field_ownership_and_compatibility_boundaries_are_explicit() -> None:
    ownership = load_json(CONTRACT_ROOT / "field-ownership.json")
    compatibility = load_json(CONTRACT_ROOT / "compatibility-matrix.json")
    manifest = load_json(CONTRACT_ROOT / "manifest.json")

    assert set(ownership["planes"]) == {
        "provider",
        "domain",
        "sse",
        "db_events",
        "conversation",
    }
    for plane, rules in ownership["planes"].items():
        claims = [item["claim"] for item in rules["owns"]]
        assert set(claims).isdisjoint(rules["forbidden"]), plane
        _unique(claims, f"{plane} owned claim")
        owned_paths = [path for item in rules["owns"] for path in item["payload_paths"]]
        _unique(owned_paths, f"{plane} owned payload path")
        _unique(rules["forbidden"], f"{plane} forbidden field")

    surfaces = {item["surface"]: item for item in compatibility["surfaces"]}
    assert surfaces["Provider contracts"]["v1"] == "service_specific_contract_later"
    assert surfaces["scenario_frames"]["v1"] == "execution_authority"
    assert surfaces["ChatSession scenario state"]["v1"] == "compatibility_projection_only"
    assert {item["service"] for item in manifest["deferred_provider_contracts"]} == {
        "knowledge",
        "scene_skill",
        "general_skill",
    }


def test_legacy_skill_corpus_matches_hash_and_production_seed() -> None:
    corpus = load_json(CONTRACT_ROOT / "legacy-skill-corpus.json")
    assert corpus["corpus_class"] == "production_seed"
    assert check_seed_corpus() == []
    source_modules: dict[Path, ModuleType] = {}

    for entry in corpus["entries"]:
        fixture_path = REPO_ROOT / entry["fixture_path"]
        digest = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
        assert entry["content_hash"] == f"sha256:{digest}"

        source_path = REPO_ROOT / entry["source_path"]
        module = source_modules.setdefault(source_path, _load_source_module(source_path))
        source_value = getattr(module, entry["source_symbol"], None)
        assert isinstance(source_value, dict), entry["source_symbol"]
        assert source_value == load_json(fixture_path)
        assert source_value["skill_id"] == entry["skill_id"]
        assert source_value["version"] == entry["version"]


def test_interaction_blocks_are_message_embedded_agent_content_only(
    schema_documents: dict[str, dict[str, Any]],
) -> None:
    validator = Draft202012Validator(
        schema_documents["interaction-block.schema.json"], format_checker=FormatChecker()
    )
    valid_blocks = [
        {
            "schema_version": "1",
            "interaction_id": "ix-draft",
            "kind": "scheduled_draft",
            "resource_id": "draft-1",
            "state": "pending",
            "allowed_actions": ["confirm", "edit"],
            "draft_id": "draft-1",
            "source_message_id": "msg-1",
            "source_turn_id": "msg-1",
            "idempotency_key": "draft-1:v1",
            "version": 1,
            "expires_at": None,
            "extensions": {},
        },
    ]
    for block in valid_blocks:
        assert list(validator.iter_errors(block)) == []

    invalid_blocks = [
        {**valid_blocks[0], "state": "confirmed", "allowed_actions": ["confirm"]},
        {
            "schema_version": "1",
            "interaction_id": "ix-citation",
            "kind": "citation",
            "resource_id": "chunk-1",
            "state": "access_revoked",
            "allowed_actions": ["resolve_full_text"],
            "title": "Policy",
            "excerpt": "...",
            "extensions": {},
        },
        {
            "schema_version": "1",
            "interaction_id": "ix-feedback",
            "kind": "feedback",
            "resource_id": "msg-2",
            "state": "active",
            "allowed_actions": ["rate_up"],
            "message_id": "msg-2",
            "current_rating": None,
            "extensions": {},
        },
        {
            "schema_version": "1",
            "interaction_id": "ix-handoff",
            "kind": "handoff",
            "resource_id": "handoff-1",
            "state": "pending",
            "allowed_actions": ["reply"],
            "handoff_id": "handoff-1",
            "session_id": "session-1",
            "extensions": {},
        },
    ]
    for block in invalid_blocks:
        assert list(validator.iter_errors(block)), block


def test_phase_scoped_pairwise_gate_and_report_are_honest() -> None:
    pairwise = load_json(CONTRACT_ROOT / "pairwise-manifest.json")
    report = load_json(CONTRACT_ROOT / "conformance-report-phase0.json")
    legacy_profile = pairwise["coverage_profiles"]["0A_legacy"]
    provider_profile = pairwise["coverage_profiles"]["provider_contract"]

    assert legacy_profile["required_from_phase"] == "0A"
    assert provider_profile["required_from_phase"] == "provider_contract_slice"
    assert "fake_remote_contract" not in legacy_profile["provider_boundary"]
    assert "fake_remote_contract" in provider_profile["provider_boundary"]

    if not legacy_profile["cases"]:
        assert pairwise["status"] == "incomplete"
        assert report["gate_decision"] == "no_go"
        assert report["pairwise_coverage"]["status"] == "incomplete"
        assert "0A_pairwise_cases" in report["missing_assets"]

    assert report["runtime_conformance"] == "not_implemented"
    assert report["gate_decision"] == "no_go"
    assert any(item["status"] == "not_run" for item in report["frontend_baseline_results"])

    expected_determinism = {
        (variant_id, execution, hash_seed, timezone)
        for variant_id in (
            "GT01-sync",
            "GT01-sse",
            "GT01-feedback-refresh-toggle",
            "GT02-ask-refresh-continue",
            "GT03-true",
            "GT03-false",
            "GT04-merge",
            "GT13-llm-error",
            "GT15-full",
            "GT16-history",
        )
        for execution, hash_seed, timezone in (
            ("in_process", None, "controlled_utc_clock"),
            ("fresh_process", "1", "UTC"),
            ("fresh_process", "777", "America/New_York"),
        )
    }
    actual_determinism = {
        (item["scenario"], item["execution"], item["hash_seed"], item["timezone"])
        for item in report["determinism_runs"]
        if item["canonical_match"]
    }
    assert actual_determinism == expected_determinism


def test_phase_0_requirement_coverage_excludes_future_phases() -> None:
    registry = load_json(CONTRACT_ROOT / "requirement-registry.json")
    report = load_json(CONTRACT_ROOT / "conformance-report-phase0.json")
    phase_0_ids = {item["id"] for item in registry["requirements"] if item["phase"] == "0A"}
    future_ids = {item["id"] for item in registry["requirements"] if item["phase"] != "0A"}

    assert len(phase_0_ids) == report["requirement_coverage"]["required"] == 29
    assert future_ids == {"AGENT-RELAY-RESUME"}


def test_fixture_matrix_is_exact_and_existing_files_are_fully_validated() -> None:
    manifest = load_json(CONTRACT_ROOT / "manifest.json")
    report = load_json(CONTRACT_ROOT / "conformance-report-phase0.json")
    expected = expected_fixture_matrix(CONTRACT_ROOT)
    schemas, registry = schema_registry(CONTRACT_ROOT, manifest)
    ownership = load_json(CONTRACT_ROOT / "field-ownership.json")
    actual = set((CONTRACT_ROOT / "fixtures").rglob("*.json"))
    missing = set(expected) - actual
    orphans = actual - set(expected)

    assert len(expected) == 27 * 2 * 5
    assert not orphans, sorted(orphans)
    groups: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = {}
    for path in sorted(actual):
        envelope = validate_fixture(CONTRACT_ROOT, expected[path], schemas, registry)
        validate_fixture_ownership(envelope, ownership)
        key = (envelope["fixture_set"], envelope["scenario_id"], envelope["variant_id"])
        groups.setdefault(key, {})[envelope["plane"]] = envelope
    relationship_requirements = load_json(CONTRACT_ROOT / "relationship-requirements.json")
    for envelopes in groups.values():
        if set(envelopes) == set(PLANES):
            validate_fixture_relationships(envelopes, ownership, relationship_requirements)

    if missing:
        assert report["gate_decision"] == "no_go"
        assert "fixture_envelopes" in report["missing_assets"]
    else:
        assert "fixture_envelopes" not in report["missing_assets"]


def test_fixture_relationship_walker_executes_rules_and_rejects_mutations() -> None:
    ownership = load_json(CONTRACT_ROOT / "field-ownership.json")
    envelopes = _relationship_fixture_group()
    validate_fixture_relationships(envelopes, ownership)
    requirements = _relationship_requirements_for_fixture_group()
    validate_fixture_relationships(envelopes, ownership, requirements)

    mismatch = deepcopy(envelopes)
    mismatch["conversation"]["payload"]["messages"][0]["id"] = "message-other"
    with pytest.raises(AssertionError, match="join .* mismatch"):
        validate_fixture_relationships(mismatch, ownership)

    unknown_rule = deepcopy(envelopes)
    unknown_rule["domain"]["joins"][0]["rule_id"] = "legacy.unknown"
    with pytest.raises(AssertionError, match="unknown fixture relationship rule"):
        validate_fixture_relationships(unknown_rule, ownership)

    duplicate_reference = deepcopy(envelopes)
    duplicate_reference["domain"]["joins"][0]["references"][1] = deepcopy(
        duplicate_reference["domain"]["joins"][0]["references"][0]
    )
    with pytest.raises(AssertionError, match="duplicate references"):
        validate_fixture_relationships(duplicate_reference, ownership)

    outside_scope = deepcopy(envelopes)
    outside_scope["domain"]["joins"][0]["references"][0]["pointer"] = "/facts/0"
    with pytest.raises(AssertionError, match="outside rule scope"):
        validate_fixture_relationships(outside_scope, ownership)

    reversed_order = deepcopy(envelopes)
    reversed_order["sse"]["payload"]["events"][1]["sequence"] = -1
    with pytest.raises(AssertionError, match="happens-before .* violated"):
        validate_fixture_relationships(reversed_order, ownership)

    self_reference = deepcopy(envelopes)
    self_reference["sse"]["happens_before"][0]["after"] = deepcopy(
        self_reference["sse"]["happens_before"][0]["before"]
    )
    with pytest.raises(AssertionError, match="self-references"):
        validate_fixture_relationships(self_reference, ownership)

    wrong_role = deepcopy(envelopes)
    wrong_role["domain"]["joins"][0]["references"][0]["role"] = "wrong_role"
    with pytest.raises(AssertionError, match="reference roles mismatch"):
        validate_fixture_relationships(wrong_role, ownership, requirements)

    wrong_path = deepcopy(envelopes)
    wrong_path["domain"]["joins"][0]["references"][0]["pointer"] = "/outcome/message_id"
    wrong_path["domain"]["payload"]["outcome"]["message_id"] = "message-1"
    with pytest.raises(AssertionError, match="endpoint path mismatch"):
        validate_fixture_relationships(wrong_path, ownership, requirements)

    wrong_event_semantics = deepcopy(envelopes)
    wrong_event_semantics["sse"]["payload"]["events"][0]["event"] = "status"
    with pytest.raises(AssertionError, match="endpoint qualifier mismatch"):
        validate_fixture_relationships(wrong_event_semantics, ownership, requirements)

    required_but_missing = {
        "reference_profiles": [
            {
                "id": "legacy.required-missing",
                "kind": "join",
                "rule_id": "legacy.turn_message_identity",
                "references": [
                    {
                        "role": "domain_turn_id",
                        "plane": "domain",
                        "pointer_pattern": "/request/turn_id",
                    },
                    {
                        "role": "conversation_message_id",
                        "plane": "conversation",
                        "pointer_pattern": "/messages/*/id",
                    },
                ],
            }
        ],
        "variants": [
            {
                "fixture_set": "legacy_characterization",
                "variant_id": "GT01-sse",
                "relations": [
                    {
                        "kind": "join",
                        "plane": "domain",
                        "name": "required-missing-join",
                        "profile_id": "legacy.required-missing",
                    }
                ],
            }
        ],
    }
    with pytest.raises(AssertionError, match="missing required fixture relationships"):
        validate_fixture_relationships(envelopes, ownership, required_but_missing)

    no_variant_requirement = {"reference_profiles": [], "variants": []}
    with pytest.raises(AssertionError, match="missing relationship requirements for"):
        validate_fixture_relationships(envelopes, ownership, no_variant_requirement)


def test_field_ownership_rejects_unresolved_forbidden_and_observation_drift() -> None:
    ownership = load_json(CONTRACT_ROOT / "field-ownership.json")
    envelope = {
        "fixture_set": "legacy_characterization",
        "plane": "domain",
        "applicability": "required",
        "payload": {
            "request": {"message": "hello"},
            "router_decision": None,
            "step_result": None,
            "tool_result": None,
            "outcome": {"reply": "world"},
            "facts": [],
        },
        "legacy_field_observation": {
            "interaction_id": "absent",
            "run_id": "absent",
        },
    }
    validate_fixture_ownership(envelope, ownership)

    unresolved = deepcopy(envelope)
    del unresolved["payload"]["outcome"]
    with pytest.raises(AssertionError, match="turn_outcome.*unresolved payload paths"):
        validate_fixture_ownership(unresolved, ownership)

    forbidden = deepcopy(envelope)
    forbidden["payload"]["request"]["provider_credentials"] = {"token": "secret"}
    with pytest.raises(AssertionError, match="forbidden fields found.*provider_credentials"):
        validate_fixture_ownership(forbidden, ownership)

    foreign_conversation_field = deepcopy(envelope)
    foreign_conversation_field["payload"]["request"]["interaction_blocks"] = []
    with pytest.raises(AssertionError, match="forbidden fields found.*interaction_blocks"):
        validate_fixture_ownership(foreign_conversation_field, ownership)

    provider_alias = {
        "fixture_set": "legacy_characterization",
        "plane": "provider",
        "applicability": "required",
        "payload": {
            "exchanges": [
                {
                    "service": "knowledge",
                    "boundary_id": "knowledge.search",
                    "source_symbol": "search",
                    "operation": "search",
                    "request": {},
                    "result": {"session": {"active_skill_id": "skill-1"}},
                    "error": None,
                }
            ]
        },
        "legacy_field_observation": {
            "interaction_id": "absent",
            "run_id": "absent",
        },
    }
    with pytest.raises(AssertionError, match="forbidden fields found.*active_skill_id"):
        validate_fixture_ownership(provider_alias, ownership)

    observation_drift = deepcopy(envelope)
    observation_drift["legacy_field_observation"]["interaction_id"] = "present"
    with pytest.raises(AssertionError, match="differ from manifest"):
        validate_fixture_ownership(observation_drift, ownership)


def _relationship_fixture_group() -> dict[str, dict[str, Any]]:
    base = {
        "fixture_set": "legacy_characterization",
        "scenario_id": "GT01",
        "variant_id": "GT01-sse",
        "applicability": "required",
        "joins": [],
        "happens_before": [],
    }
    envelopes = {plane: {**deepcopy(base), "plane": plane, "payload": {}} for plane in PLANES}
    envelopes["provider"]["payload"] = {"exchanges": []}
    envelopes["domain"]["payload"] = {
        "request": {"turn_id": "message-1"},
        "outcome": {"message_id": "message-1"},
        "facts": [],
    }
    envelopes["sse"]["payload"] = {
        "events": [
            {"sequence": 0, "event": "user_message_received"},
            {"sequence": 1, "event": "complete"},
        ]
    }
    envelopes["db_events"]["payload"] = {
        "events": [
            {"observed_row_order": 0, "payload": {"turn_id": "message-1"}},
            {"observed_row_order": 1, "payload": {"message_id": "assistant-1"}},
        ]
    }
    envelopes["conversation"]["payload"] = {
        "messages": [{"id": "message-1"}, {"id": "assistant-1"}]
    }
    envelopes["domain"]["joins"] = [
        {
            "name": "turn-message",
            "rule_id": "legacy.turn_message_identity",
            "references": [
                {
                    "role": "domain_turn_id",
                    "plane": "domain",
                    "pointer": "/request/turn_id",
                },
                {
                    "role": "db_turn_id",
                    "plane": "db_events",
                    "pointer": "/events/0/payload/turn_id",
                },
                {
                    "role": "conversation_message_id",
                    "plane": "conversation",
                    "pointer": "/messages/0/id",
                },
            ],
        }
    ]
    envelopes["sse"]["happens_before"] = [
        {
            "name": "first-before-terminal",
            "rule_id": "legacy.observed_event_order",
            "order_type": "integer",
            "before": {
                "role": "first_event_sequence",
                "plane": "sse",
                "pointer": "/events/0/sequence",
            },
            "after": {
                "role": "terminal_event_sequence",
                "plane": "sse",
                "pointer": "/events/1/sequence",
            },
        }
    ]
    return envelopes


def _relationship_requirements_for_fixture_group() -> dict[str, Any]:
    return {
        "reference_profiles": [
            {
                "id": "legacy.test-turn-message",
                "kind": "join",
                "rule_id": "legacy.turn_message_identity",
                "references": [
                    {
                        "role": "domain_turn_id",
                        "plane": "domain",
                        "pointer_pattern": "/request/turn_id",
                    },
                    {
                        "role": "db_turn_id",
                        "plane": "db_events",
                        "pointer_pattern": "/events/*/payload/turn_id",
                    },
                    {
                        "role": "conversation_message_id",
                        "plane": "conversation",
                        "pointer_pattern": "/messages/*/id",
                    },
                ],
            },
            {
                "id": "legacy.test-order",
                "kind": "happens_before",
                "rule_id": "legacy.observed_event_order",
                "order_type": "integer",
                "before": {
                    "role": "first_event_sequence",
                    "plane": "sse",
                    "pointer_pattern": "/events/*/sequence",
                    "qualifier": {
                        "levels_up": 1,
                        "relative_pointer": "/event",
                        "equals": "user_message_received",
                    },
                },
                "after": {
                    "role": "terminal_event_sequence",
                    "plane": "sse",
                    "pointer_pattern": "/events/*/sequence",
                    "qualifier": {
                        "levels_up": 1,
                        "relative_pointer": "/event",
                        "equals": "complete",
                    },
                },
            },
        ],
        "variants": [
            {
                "fixture_set": "legacy_characterization",
                "variant_id": "GT01-sse",
                "relations": [
                    {
                        "kind": "join",
                        "plane": "domain",
                        "name": "turn-message",
                        "profile_id": "legacy.test-turn-message",
                    },
                    {
                        "kind": "happens_before",
                        "plane": "sse",
                        "name": "first-before-terminal",
                        "profile_id": "legacy.test-order",
                    },
                ],
            }
        ],
    }


def test_conformance_go_is_derived_from_evidence() -> None:
    report = load_json(CONTRACT_ROOT / "conformance-report-phase0.json")
    failures = conformance_gate_failures(report)
    assert failures
    assert report["gate_decision"] == "no_go"

    dishonest = {**report, "gate_decision": "go", "gate_reasons": []}
    assert set(conformance_gate_failures(dishonest)) == {
        *failures,
        "gate_decision",
    }

    nondeterministic = deepcopy(report)
    nondeterministic["determinism_runs"][0]["canonical_match"] = False
    assert "determinism_runs" in conformance_gate_failures(nondeterministic)

    manual_not_required = deepcopy(report)
    for item in manual_not_required["frontend_baseline_results"]:
        item["status"] = "pass"
        item["evidence_mode"] = "automated"
    manual_item = next(
        item
        for item in manual_not_required["frontend_baseline_results"]
        if item["id"] == "manual_chat_frontend_desktop_browser"
    )
    manual_item["evidence_mode"] = "manual_observation"
    manual_item["gate_required"] = False
    assert "frontend_baseline_results" not in conformance_gate_failures(manual_not_required)
    manual_item["gate_required"] = True
    assert "frontend_baseline_results" in conformance_gate_failures(manual_not_required)

    complete = deepcopy(report)
    complete["legacy_characterization_status"] = "complete"
    for field in (
        "schema_validation_results",
        "scenario_plane_results",
        "join_invariant_results",
        "happens_before_results",
        "http_sse_harness_results",
        "frontend_baseline_results",
    ):
        for item in complete[field]:
            item["status"] = "pass"
            item["evidence_mode"] = "automated"
            item["gate_required"] = True
    for field in (
        "requirement_coverage",
        "vocabulary_coverage",
        "pairwise_coverage",
        "seam_coverage",
        "corpus_coverage",
    ):
        complete[field]["status"] = "complete"
        complete[field]["covered"] = complete[field]["required"]
        complete[field]["missing"] = []
    complete["missing_assets"] = []
    complete["reviews"] = [
        {"role": "architecture", "status": "approved", "runtime_gate": "go"},
        {"role": "qa", "status": "approved", "runtime_gate": "go"},
    ]
    complete["gate_decision"] = "no_go"
    complete["gate_reasons"] = ["stale manual decision"]
    assert conformance_gate_failures(complete) == ["gate_decision"]
    complete["gate_decision"] = "go"
    complete["gate_reasons"] = []
    assert conformance_gate_failures(complete) == []


def test_report_artifact_hashes_are_current_and_include_manifest_closure() -> None:
    report = load_json(CONTRACT_ROOT / "conformance-report-phase0.json")
    for relative_path, expected_hash in report["artifact_hashes"].items():
        if relative_path == "manifest_closure":
            continue
        digest = hashlib.sha256((CONTRACT_ROOT / relative_path).read_bytes()).hexdigest()
        assert expected_hash == f"sha256:{digest}", relative_path
    assert report["artifact_hashes"].get("manifest_closure") == manifest_closure_hash(CONTRACT_ROOT)
