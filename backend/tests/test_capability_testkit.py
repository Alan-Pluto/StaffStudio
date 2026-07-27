import pytest

from app.capabilities.contracts import KnowledgeSearchResult, SkillExecutionResult
from app.capabilities.errors import CapabilityErrorInfo
from app.capabilities.testkit import (
    ContractViolation,
    assert_knowledge_search_result,
    assert_namespaced_extensions,
    assert_provider_error,
    assert_skill_execution_result,
)


def test_testkit_accepts_service_owned_extensions_and_terminal_skill_result() -> None:
    assert_knowledge_search_result(
        KnowledgeSearchResult(query_id="q1", extensions={"vendor_x": {"score": 1}})
    )
    assert_skill_execution_result(
        SkillExecutionResult(execution_id="exec-1", state="failed", error_code="SKILL_FAILED")
    )
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


def test_testkit_requires_error_code_for_failed_execution() -> None:
    with pytest.raises(ContractViolation, match="error_code"):
        assert_skill_execution_result(SkillExecutionResult(execution_id="exec-1", state="failed"))


def test_testkit_requires_request_id_for_provider_errors() -> None:
    with pytest.raises(ContractViolation, match="request_id"):
        assert_provider_error(CapabilityErrorInfo(code="X", message="bad", retryable=False))
