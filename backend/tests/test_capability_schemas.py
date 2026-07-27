import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_DIR = Path(__file__).parents[1] / "app" / "capabilities" / "schemas"


def load_schema(name: str) -> dict[str, object]:
    return json.loads((SCHEMA_DIR / name).read_text())


def test_knowledge_request_schema_rejects_unknown_top_level_fields() -> None:
    schema = load_schema("knowledge.search.request.v1.json")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    valid = {
        "context": {
            "request_id": "req-1",
            "tenant_id": "tenant-1",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "channel": "web",
        },
        "query": "policy",
    }
    assert not list(validator.iter_errors(valid))
    invalid = {**valid, "provider_secret": "must-not-cross-contract"}
    assert list(validator.iter_errors(invalid))


def test_knowledge_result_schema_allows_namespaced_extensions_only() -> None:
    schema = load_schema("knowledge.search.result.v1.json")
    validator = Draft202012Validator(schema)
    valid = {
        "query_id": "q-1",
        "items": [],
        "outcome": "complete",
        "warnings": [],
        "extensions": {"vendor_x": {"rerank": 0.9}},
    }
    assert not list(validator.iter_errors(valid))
    invalid = {**valid, "extensions": {"Vendor-X": {}}}
    assert list(validator.iter_errors(invalid))


def test_provider_error_schema_requires_retryability() -> None:
    schema = load_schema("provider.error.v1.json")
    validator = Draft202012Validator(schema)
    assert list(validator.iter_errors({"error_code": "X"}))
