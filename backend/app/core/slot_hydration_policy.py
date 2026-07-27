from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.db.models import ChatSession, Skill
from app.session.session_schema import RouterDecision


class SlotHydrationPolicy:
    @classmethod
    def hydrate(
        cls,
        chat_session: ChatSession,
        router_decision: RouterDecision,
        skills: list[Skill],
        memory_context: list[dict[str, object]],
        patcher: Callable[
            [Skill | None, dict[str, Any], list[dict[str, object]]], dict[str, Any]
        ]
        | None = None,
        awaiting_trimmer: Callable[
            [RouterDecision, dict[str, Any]], list[str] | None
        ]
        | None = None,
    ) -> dict[str, Any]:
        patch_slots = patcher or cls.patch
        trim_awaiting = awaiting_trimmer or cls.trim_satisfied_awaiting_fields
        skills_by_id = {skill.skill_id: skill for skill in skills}
        hydrated: dict[str, Any] = {}
        target_skill = skills_by_id.get(
            router_decision.target_skill_id or chat_session.active_skill_id or ""
        )
        base_slots = dict(chat_session.slots_json or {})
        base_slots.update(dict(router_decision.slot_hints or {}))
        patch = patch_slots(target_skill, base_slots, memory_context)
        if patch:
            router_decision.slot_hints = {**dict(router_decision.slot_hints or {}), **patch}
            hydrated["primary"] = patch
        remaining_awaiting = trim_awaiting(router_decision, {**base_slots, **patch})
        if remaining_awaiting is not None:
            hydrated["awaiting_input_expected_fields"] = remaining_awaiting

        task_patches: list[dict[str, Any]] = []
        for task in [
            *router_decision.task_frames,
            *router_decision.pending_tasks,
            *router_decision.created_tasks,
        ]:
            task_skill = skills_by_id.get(task.target_skill_id or "")
            task_slots = dict(task.slot_hints or {})
            task_patch = patch_slots(task_skill, task_slots, memory_context)
            if task_patch:
                task.slot_hints = {**task_slots, **task_patch}
                task_patches.append(
                    {
                        "task_id": task.task_id,
                        "target_skill_id": task.target_skill_id,
                        "slots": task_patch,
                    }
                )
        if task_patches:
            hydrated["tasks"] = task_patches
        return hydrated

    @classmethod
    def patch(
        cls,
        skill: Skill | None,
        slots: dict[str, Any],
        memory_context: list[dict[str, object]],
    ) -> dict[str, Any]:
        if not skill:
            return {}
        expected_fields = cls.skill_expected_fields(skill)
        patch: dict[str, Any] = {}
        if "user_name" in expected_fields and not cls.slot_has_value(slots, "user_name"):
            profile_name = cls.profile_name_from_memory(memory_context)
            if profile_name:
                patch["user_name"] = profile_name
        return patch

    @classmethod
    def trim_satisfied_awaiting_fields(
        cls, router_decision: RouterDecision, slots: dict[str, Any]
    ) -> list[str] | None:
        if not router_decision.awaiting_input:
            return None
        original = list(router_decision.awaiting_input.expected_fields)
        remaining = [
            field
            for field in router_decision.awaiting_input.expected_fields
            if not cls.slot_has_value(slots, field)
        ]
        if remaining == original:
            return None
        if remaining:
            router_decision.awaiting_input.expected_fields = remaining
        else:
            router_decision.awaiting_input = None
        return remaining

    @staticmethod
    def slot_has_value(slots: dict[str, Any], field: str) -> bool:
        value = slots.get(field)
        return value is not None and value != "" and value != []

    @staticmethod
    def skill_expected_fields(skill: Skill) -> set[str]:
        content = skill.content_json or {}
        fields: set[str] = set()
        required_info = content.get("required_info")
        if isinstance(required_info, list):
            fields.update(str(item) for item in required_info if str(item).strip())
        nodes = content.get("nodes")
        if isinstance(nodes, list):
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                expected = node.get("expected_user_info")
                if isinstance(expected, list):
                    fields.update(str(item) for item in expected if str(item).strip())
        return fields

    @staticmethod
    def profile_name_from_memory(memory_context: list[dict[str, object]]) -> str:
        for memory in memory_context:
            if memory.get("kind") != "profile":
                continue
            metadata = memory.get("metadata")
            key = metadata.get("key") if isinstance(metadata, dict) else None
            content = str(memory.get("content") or "").strip()
            if key == "preferred_name" and content:
                return content[:40]
        return ""
