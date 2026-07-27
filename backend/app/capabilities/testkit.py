from __future__ import annotations

import re
from collections.abc import Mapping

from app.capabilities.contracts import KnowledgeSearchResult, SkillExecutionResult
from app.capabilities.errors import CapabilityErrorInfo

_EXTENSION_NAMESPACE = re.compile(r"^[a-z][a-z0-9_]*$")


class ContractViolation(AssertionError):
    """Raised by the shared Provider Contract Test Kit."""


def assert_namespaced_extensions(extensions: Mapping[str, object]) -> None:
    for namespace in extensions:
        if not _EXTENSION_NAMESPACE.fullmatch(namespace):
            raise ContractViolation(
                f"extension namespace must be lowercase snake case: {namespace!r}"
            )


def assert_knowledge_search_result(result: KnowledgeSearchResult) -> None:
    if not result.query_id:
        raise ContractViolation("Knowledge result query_id is required")
    if result.outcome not in {"complete", "partial"}:
        raise ContractViolation(f"unknown Knowledge outcome: {result.outcome!r}")
    assert_namespaced_extensions(result.extensions)
    for item in result.items:
        if not item.hit_id or not item.content:
            raise ContractViolation("Knowledge hits require hit_id and content")


def assert_skill_execution_result(result: SkillExecutionResult) -> None:
    states = {"queued", "running", "cancelling", "succeeded", "failed", "cancelled"}
    if not result.execution_id:
        raise ContractViolation("Skill execution_id is required")
    if result.state not in states:
        raise ContractViolation(f"unknown Skill execution state: {result.state!r}")
    if result.state == "failed" and not result.error_code:
        raise ContractViolation("failed Skill executions require error_code")
    assert_namespaced_extensions(result.extensions)


def assert_provider_error(info: CapabilityErrorInfo) -> None:
    if not info.code or not info.message:
        raise ContractViolation("Provider errors require code and message")
    if not isinstance(info.retryable, bool):
        raise ContractViolation("Provider error retryable must be boolean")
