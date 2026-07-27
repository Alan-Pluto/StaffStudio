"""Typed ports for externally provided Agent capabilities.

This package intentionally keeps Knowledge, Scene Skill and General Skill
contracts separate.  The legacy AgentLoop is not coupled to these ports yet;
adapters can be introduced behind the registry without changing its behavior.
"""

from app.capabilities.contracts import (
    CapabilityContext,
    CitationDetail,
    GeneralSkillCatalog,
    GeneralSkillExecutionRequest,
    GeneralSkillExecutor,
    GeneralSkillPackage,
    GeneralSkillResourceRef,
    KnowledgeRuntime,
    KnowledgeScope,
    KnowledgeSearchQuery,
    KnowledgeSearchResult,
    SceneSkillCatalog,
    SceneSkillDefinition,
    SkillArtifact,
    SkillExecutionEvent,
    SkillExecutionEventPage,
)
from app.capabilities.errors import CapabilityErrorInfo, CapabilityProviderError
from app.capabilities.local_knowledge import LocalKnowledgeRuntime
from app.capabilities.registry import (
    CapabilityBinding,
    CapabilityRegistry,
    CapabilitySnapshot,
    DurableCapabilityBinding,
)
from app.capabilities.testkit import ContractViolation

__all__ = [
    "CapabilityBinding",
    "CapabilityContext",
    "CapabilityErrorInfo",
    "CapabilityProviderError",
    "CapabilityRegistry",
    "CapabilitySnapshot",
    "CitationDetail",
    "ContractViolation",
    "DurableCapabilityBinding",
    "GeneralSkillCatalog",
    "GeneralSkillExecutionRequest",
    "GeneralSkillExecutor",
    "GeneralSkillPackage",
    "GeneralSkillResourceRef",
    "KnowledgeRuntime",
    "KnowledgeScope",
    "KnowledgeSearchQuery",
    "KnowledgeSearchResult",
    "LocalKnowledgeRuntime",
    "SceneSkillCatalog",
    "SceneSkillDefinition",
    "SkillArtifact",
    "SkillExecutionEvent",
    "SkillExecutionEventPage",
]
