from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol, TypeAlias

JsonValue: TypeAlias = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)
JsonObject: TypeAlias = Mapping[str, JsonValue]
ExtensionMap: TypeAlias = Mapping[str, JsonObject]


@dataclass(frozen=True)
class CapabilityContext:
    """Trusted Core invocation context passed through a Provider adapter.

    The session/turn/user/agent/channel fields are the stable Provider business
    context. Tenant and transport metadata are injected by Core and are not a
    second public Provider identity model.
    """

    request_id: str
    tenant_id: str
    agent_id: str
    user_id: str
    session_id: str
    turn_id: str
    channel: str
    trace_id: str | None = None
    deadline_at: datetime | None = None
    attempt: int = 1

    def __post_init__(self) -> None:
        required = {
            "request_id": self.request_id,
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "channel": self.channel,
        }
        missing = [
            name
            for name, value in required.items()
            if not isinstance(value, str) or not value.strip()
        ]
        if missing:
            raise ValueError(
                "CapabilityContext requires non-empty fields: " + ", ".join(missing)
            )
        if (
            not isinstance(self.attempt, int)
            or isinstance(self.attempt, bool)
            or self.attempt < 1
        ):
            raise ValueError("CapabilityContext attempt must be a positive integer")


@dataclass(frozen=True)
class GeneralSkillResourceRef:
    """Pinned catalog resource passed to an executor; never resolve by slug at run time."""

    catalog_binding_id: str
    package_id: str
    version: str
    digest: str
    package_contract_version: str
    package_store_record_id: str | None = None


@dataclass(frozen=True)
class KnowledgeSearchQuery:
    query: str
    query_type: str = "answer"
    scope: Mapping[str, JsonValue] = field(default_factory=dict)
    max_chunks: int = 8
    budget_tokens: int = 4000
    cursor: str | None = None


@dataclass(frozen=True)
class KnowledgeScope:
    scope_id: str
    name: str
    version: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeHit:
    hit_id: str
    content: str
    score: float | None = None
    source_ref: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeSearchResult:
    """Knowledge-owned result; it does not carry Skill execution fields."""

    query_id: str
    items: tuple[KnowledgeHit, ...] = ()
    outcome: Literal["complete", "partial"] = "complete"
    warnings: tuple[str, ...] = ()
    next_cursor: str | None = None
    service_version: str | None = None
    extensions: ExtensionMap = field(default_factory=dict)


@dataclass(frozen=True)
class CitationDetail:
    citation_id: str
    title: str | None
    content: str | None
    source_ref: str | None = None
    extensions: ExtensionMap = field(default_factory=dict)


@dataclass(frozen=True)
class SceneSkillSummary:
    skill_id: str
    version: str
    name: str
    definition_hash: str
    extensions: ExtensionMap = field(default_factory=dict)


@dataclass(frozen=True)
class SceneSkillDefinition:
    skill_id: str
    version: str
    definition_hash: str
    start_step_id: str
    steps: tuple[Mapping[str, JsonValue], ...]
    edges: tuple[Mapping[str, JsonValue], ...] = ()
    extensions: ExtensionMap = field(default_factory=dict)
    terminal_node_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class GeneralSkillSummary:
    slug: str
    version: str
    name: str
    package_id: str
    digest: str
    extensions: ExtensionMap = field(default_factory=dict)


@dataclass(frozen=True)
class GeneralSkillPackage:
    package_id: str
    slug: str
    version: str
    digest: str
    package_contract_version: str
    entrypoint: str
    input_schema: Mapping[str, JsonValue] = field(default_factory=dict)
    output_schema: Mapping[str, JsonValue] = field(default_factory=dict)
    extensions: ExtensionMap = field(default_factory=dict)


@dataclass(frozen=True)
class GeneralSkillExecutionRequest:
    resource_ref: GeneralSkillResourceRef
    input: Mapping[str, JsonValue]
    idempotency_key: str
    execution_deadline_at: datetime | None = None
    requested_artifacts: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillExecutionRef:
    execution_id: str
    package_id: str
    version: str
    digest: str
    executor_binding_id: str
    idempotency_key: str


@dataclass(frozen=True)
class SkillArtifact:
    artifact_id: str
    execution_id: str
    kind: str
    content_type: str
    size: int
    digest: str
    expires_at: datetime | None = None
    state: Literal["available", "expired", "revoked"] = "available"


@dataclass(frozen=True)
class SkillExecutionEvent:
    event_id: str
    execution_id: str
    sequence: int
    kind: str
    occurred_at: datetime
    payload: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillExecutionEventPage:
    execution_id: str
    events: tuple[SkillExecutionEvent, ...] = ()
    next_cursor: str | None = None
    cursor_expires_at: datetime | None = None


@dataclass(frozen=True)
class SkillExecutionResult:
    execution_id: str
    state: Literal["queued", "running", "cancelling", "succeeded", "failed", "cancelled"]
    summary: str | None = None
    artifacts: tuple[SkillArtifact, ...] = ()
    error_code: str | None = None
    last_event_cursor: str | None = None
    extensions: ExtensionMap = field(default_factory=dict)


class KnowledgeRuntime(Protocol):
    provider_id: str

    def list_scopes(self, context: CapabilityContext) -> Sequence[KnowledgeScope]: ...

    def search(self, context: CapabilityContext, request: KnowledgeSearchQuery) -> KnowledgeSearchResult: ...

    def resolve_citation(self, context: CapabilityContext, provider_citation_ref: str) -> CitationDetail: ...


class SceneSkillCatalog(Protocol):
    provider_id: str

    def list_published(self, context: CapabilityContext) -> Sequence[SceneSkillSummary]: ...

    def get_definition(self, context: CapabilityContext, skill_id: str, version: str | None = None) -> SceneSkillDefinition | None: ...


class GeneralSkillCatalog(Protocol):
    provider_id: str

    def list_published(self, context: CapabilityContext) -> Sequence[GeneralSkillSummary]: ...

    def get_package(self, context: CapabilityContext, slug: str, version: str | None = None) -> GeneralSkillPackage | None: ...


class GeneralSkillExecutor(Protocol):
    provider_id: str

    def start_execution(self, context: CapabilityContext, request: GeneralSkillExecutionRequest) -> SkillExecutionRef: ...

    def get_execution(self, context: CapabilityContext, execution_ref: SkillExecutionRef) -> SkillExecutionResult: ...

    def list_events(
        self,
        context: CapabilityContext,
        execution_ref: SkillExecutionRef,
        after_cursor: str | None = None,
    ) -> SkillExecutionEventPage: ...

    def cancel_execution(self, context: CapabilityContext, execution_ref: SkillExecutionRef, command_id: str) -> SkillExecutionResult: ...
