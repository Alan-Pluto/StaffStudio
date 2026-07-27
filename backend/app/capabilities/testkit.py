from __future__ import annotations

import json
import re
from collections.abc import Mapping

from app.capabilities.contracts import GeneralSkillPackage, KnowledgeSearchResult
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


def assert_general_skill_package(package: GeneralSkillPackage) -> None:
    required = {
        "package_id": package.package_id,
        "slug": package.slug,
        "version": package.version,
        "digest": package.digest,
        "package_contract_version": package.package_contract_version,
        "skill_markdown": package.skill_markdown,
        "entrypoint": package.entrypoint,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ContractViolation(
            "General Skill package requires non-empty fields: " + ", ".join(missing)
        )
    if not package.files:
        raise ContractViolation("General Skill package requires files")
    paths: set[str] = set()
    for item in package.files:
        if not item.path or not isinstance(item.content, str):
            raise ContractViolation("General Skill files require path and string content")
        if item.path in paths:
            raise ContractViolation(f"duplicate General Skill file path: {item.path}")
        if item.size is not None and item.size < 0:
            raise ContractViolation("General Skill file size cannot be negative")
        paths.add(item.path)
    if package.entrypoint not in paths:
        raise ContractViolation("General Skill entrypoint must reference a package file")
    assert_namespaced_extensions(package.extensions)


def assert_provider_error(info: CapabilityErrorInfo) -> None:
    if not info.code or not info.message or not info.request_id:
        raise ContractViolation("Provider errors require code, message and request_id")
    if not isinstance(info.retryable, bool):
        raise ContractViolation("Provider error retryable must be boolean")
