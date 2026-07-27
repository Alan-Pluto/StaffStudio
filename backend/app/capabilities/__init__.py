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
    KnowledgeSearchQuery,
    KnowledgeSearchResult,
    SceneSkillCatalog,
    SceneSkillDefinition,
)
from app.capabilities.registry import (
    CapabilityBinding,
    CapabilityRegistry,
    CapabilitySnapshot,
)

__all__ = [
    "CapabilityBinding",
    "CapabilityContext",
    "CapabilityRegistry",
    "CapabilitySnapshot",
    "CitationDetail",
    "GeneralSkillCatalog",
    "GeneralSkillExecutionRequest",
    "GeneralSkillExecutor",
    "GeneralSkillPackage",
    "GeneralSkillResourceRef",
    "KnowledgeRuntime",
    "KnowledgeSearchQuery",
    "KnowledgeSearchResult",
    "SceneSkillCatalog",
    "SceneSkillDefinition",
]
