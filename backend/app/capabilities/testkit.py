from __future__ import annotations

import json
import re
from collections.abc import Mapping

from app.capabilities.contracts import KnowledgeSearchResult, SkillExecutionResult
from app.capabilities.errors import CapabilityErrorInfo

_EXTENSION_NAMESPACE = re.compile(r"^[a-z][a-z0-9_]*$")
_RESERVED_NAMESPACES = {"core", "staffdeck"}


class ContractViolation(AssertionError):
    """Raised by the shared Provider Contract Test Kit."""


def assert_namespaced_extensions(extensions: Mapping[str, object]) -> None:
    for namespace in extensions:
        if not _EXTENSION_NAMESPACE.fullmatch(namespace) or namespace in _RESERVED_NAMESPACES:
            raise ContractViolation(
                f"extension namespace must be lowercase snake case: {namespace!r}"
            )
        if not isinstance(extensions[namespace], Mapping):
            raise ContractViolation("each extension namespace must contain an object")
    try:
        encoded = json.dumps(extensions, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ContractViolation("extensions must contain JSON values") from exc
    if len(encoded.encode("utf-8")) > 64 * 1024:
        raise ContractViolation("extensions exceed the 64 KiB contract limit")


def assert_knowledge_search_result(result: KnowledgeSearchResult) -> None:
    if not result.query_id:
        raise ContractViolation("Knowledge result query_id is required")
    if result.outcome not in {"complete", "partial"}:
        raise ContractViolation(f"unknown Knowledge outcome: {result.outcome!r}")
    assert_namespaced_extensions(result.extensions)
    hit_ids: set[str] = set()
    for item in result.items:
        if not item.hit_id or not item.content:
            raise ContractViolation("Knowledge hits require hit_id and content")
        if item.hit_id in hit_ids:
            raise ContractViolation(f"duplicate Knowledge hit_id: {item.hit_id}")
        hit_ids.add(item.hit_id)
        if item.score is not None and not isinstance(item.score, (int, float)):
            raise ContractViolation("Knowledge hit score must be numeric")
        if item.source_ref is not None and not isinstance(item.source_ref, str):
            raise ContractViolation("Knowledge hit source_ref must be a string")


def assert_skill_execution_result(result: SkillExecutionResult) -> None:
    states = {"queued", "running", "cancelling", "succeeded", "failed", "cancelled"}
    if not result.execution_id:
        raise ContractViolation("Skill execution_id is required")
    if result.state not in states:
        raise ContractViolation(f"unknown Skill execution state: {result.state!r}")
    if result.state == "failed" and not result.error_code:
        raise ContractViolation("failed Skill executions require error_code")
    if result.state in {"succeeded", "cancelled"} and result.error_code:
        raise ContractViolation("successful/cancelled Skill executions cannot carry error_code")
    if result.state in {"queued", "running", "cancelling"} and result.error_code:
        raise ContractViolation("non-terminal Skill executions cannot carry error_code")
    for artifact in result.artifacts:
        if artifact.execution_id != result.execution_id:
            raise ContractViolation("Skill artifact belongs to a different execution")
        if not artifact.artifact_id or not artifact.kind or not artifact.content_type:
            raise ContractViolation("Skill artifacts require id, kind and content_type")
        if artifact.size < 0 or not artifact.digest:
            raise ContractViolation("Skill artifacts require non-negative size and digest")
    assert_namespaced_extensions(result.extensions)


def assert_provider_error(info: CapabilityErrorInfo) -> None:
    if not info.code or not info.message or not info.request_id:
        raise ContractViolation("Provider errors require code, message and request_id")
    if not isinstance(info.retryable, bool):
        raise ContractViolation("Provider error retryable must be boolean")
