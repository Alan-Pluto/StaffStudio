import pytest

from app.capabilities.contracts import (
    GeneralSkillFile,
    GeneralSkillPackage,
    KnowledgeSearchResult,
)
from app.capabilities.errors import CapabilityErrorInfo
from app.capabilities.testkit import (
    ContractViolation,
    assert_general_skill_package,
    assert_knowledge_search_result,
    assert_namespaced_extensions,
    assert_provider_error,
)


def _package(**overrides: object) -> GeneralSkillPackage:
    values = {
        "package_id": "weather",
        "slug": "weather",
        "version": "1.4.0",
        "digest": "sha256:package",
        "package_contract_version": "1",
        "skill_markdown": "# Weather",
        "files": (GeneralSkillFile(path="SKILL.md", content="# Weather"),),
        "entrypoint": "SKILL.md",
        "extensions": {"vendor_x": {"runtime": "python"}},
    }
    values.update(overrides)
    return GeneralSkillPackage(**values)  # type: ignore[arg-type]


def test_testkit_accepts_service_owned_extensions_and_skill_package() -> None:
    assert_knowledge_search_result(
        KnowledgeSearchResult(query_id="q1", extensions={"vendor_x": {"score": 1}})
    )
    assert_general_skill_package(_package())
    assert_provider_error(
        CapabilityErrorInfo(
            code="X_FAILED", message="failed", retryable=False, request_id="req-1"
        )
    )


def test_testkit_rejects_non_namespaced_extensions() -> None:
    with pytest.raises(ContractViolation, match="namespace"):
        assert_namespaced_extensions({"Vendor-X": {}})

    with pytest.raises(ContractViolation, match="JSON"):
        assert_namespaced_extensions({"vendor_x": {"value": object()}})


def test_testkit_requires_entrypoint_to_reference_package_content() -> None:
    with pytest.raises(ContractViolation, match="entrypoint"):
        assert_general_skill_package(_package(entrypoint="missing.py"))


def test_testkit_requires_request_id_for_provider_errors() -> None:
    with pytest.raises(ContractViolation, match="request_id"):
        assert_provider_error(CapabilityErrorInfo(code="X", message="bad", retryable=False))
