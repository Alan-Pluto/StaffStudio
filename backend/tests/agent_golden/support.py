from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any, ClassVar


class DeterministicIdFactory:
    """Allocate stable, thread-safe identifiers while preserving join identity."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}

    def next(self, prefix: str) -> str:
        if not prefix or not prefix.replace("-", "_").isalnum():
            raise ValueError(f"invalid deterministic id prefix: {prefix!r}")
        with self._lock:
            value = self._counters.get(prefix, 0) + 1
            self._counters[prefix] = value
        return f"{prefix}_{value:04d}"

    def for_key(self, prefix: str, producer_key: str) -> str:
        if not prefix or not prefix.replace("-", "_").isalnum():
            raise ValueError(f"invalid deterministic id prefix: {prefix!r}")
        if not producer_key:
            raise ValueError("producer_key must not be empty")
        digest = hashlib.sha256(f"{prefix}\0{producer_key}".encode()).hexdigest()[:16]
        return f"{prefix}_{digest}"


class MonotonicClock:
    """Return deterministic UTC instants that are strictly increasing."""

    def __init__(
        self,
        start: datetime = datetime(2024, 1, 1, tzinfo=UTC),
        step: timedelta = timedelta(milliseconds=1),
    ) -> None:
        if start.tzinfo is None:
            raise ValueError("start must be timezone-aware")
        if step <= timedelta(0):
            raise ValueError("step must be positive")
        self._next = start.astimezone(UTC)
        self._step = step
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            current = self._next
            self._next += self._step
        return current

    def now_iso(self) -> str:
        return self.now().isoformat(timespec="milliseconds").replace("+00:00", "Z")


class CanonicalNormalizer:
    """Normalize volatile scalar values without changing collection semantics."""

    _VOLATILE_ID_KEYS: ClassVar[set[str]] = {
        "session_id",
        "sessionId",
        "newSessionId",
        "message_id",
        "user_message_id",
        "assistant_message_id",
        "turn_id",
        "event_id",
        "interaction_id",
        "run_id",
        "handoff_id",
        "draft_id",
    }
    _EXACT_TIME_KEYS: ClassVar[set[str]] = {"created_at", "updated_at", "timestamp"}

    def __init__(
        self,
        *,
        rfc3339_timestamps: bool = False,
        rules: Sequence[Mapping[str, str]] | None = None,
    ) -> None:
        self._ids: dict[str, str] = {}
        self._times: dict[str, str] = {}
        self._rfc3339_timestamps = rfc3339_timestamps
        self._rules = list(rules) if rules is not None else None

    @classmethod
    def from_profile(
        cls,
        path: Path,
        profile_id: str,
        *,
        rfc3339_timestamps: bool = False,
    ) -> CanonicalNormalizer:
        document = load_json(path)
        profiles = [item for item in document["profiles"] if item["id"] == profile_id]
        if len(profiles) != 1:
            raise ValueError(f"expected exactly one normalization profile {profile_id!r}")
        rules = profiles[0]["rules"]
        supported = {
            "identity_map",
            "monotonic_time_map",
            "duration_placeholder",
            "preserve",
        }
        unsupported = {item["strategy"] for item in rules} - supported
        if unsupported:
            raise ValueError(f"unsupported normalization strategies: {sorted(unsupported)}")
        return cls(rfc3339_timestamps=rfc3339_timestamps, rules=rules)

    def normalize(self, value: Any) -> Any:
        self._register_times(value)
        return self._normalize(deepcopy(value), key=None, path=())

    def dumps(self, value: Any) -> str:
        normalized = self.normalize(value)
        return (
            json.dumps(
                normalized,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )

    def _normalize(self, value: Any, *, key: str | None, path: tuple[str | int, ...]) -> Any:
        if isinstance(value, Mapping):
            return {
                item_key: self._normalize(
                    item_value,
                    key=item_key,
                    path=(*path, item_key),
                )
                for item_key, item_value in value.items()
            }
        if isinstance(value, list):
            return [
                self._normalize(item, key=key, path=(*path, index))
                for index, item in enumerate(value)
            ]
        if value is None:
            return None
        strategy = self._strategy(path, key)
        if isinstance(value, str):
            if strategy == "identity_map":
                return self._stable_map(self._ids, value, "id")
            if strategy == "monotonic_time_map":
                return self._stable_map(self._times, value, "time")
        if strategy == "duration_placeholder" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return "<duration_ms>"
        return value

    def _strategy(
        self, path: tuple[str | int, ...], key: str | None
    ) -> str | None:
        if self._rules is not None:
            rendered = self._render_path(path)
            return next(
                (
                    rule["strategy"]
                    for rule in self._rules
                    if self._path_matches(rendered, rule["match"])
                ),
                None,
            )
        if key is not None:
            if key in self._VOLATILE_ID_KEYS or self._is_contextual_id_path(path):
                return "identity_map"
            if key in self._EXACT_TIME_KEYS or key.endswith("_at"):
                return "monotonic_time_map"
            if key == "duration_ms":
                return "duration_placeholder"
        return None

    @staticmethod
    def _render_path(path: tuple[str | int, ...]) -> str:
        rendered = "$"
        for token in path:
            rendered += f"[{token}]" if isinstance(token, int) else f".{token}"
        return rendered

    @staticmethod
    def _path_matches(path: str, pattern: str) -> bool:
        expression = re.escape(pattern)
        expression = expression.replace(r"\*\*", "__RECURSIVE__")
        expression = expression.replace(r"\[\*\]", r"\[\d+\]")
        expression = expression.replace(r"\*", r"[^.\[]+")
        expression = expression.replace("__RECURSIVE__", ".*")
        return re.fullmatch(expression, path) is not None

    @staticmethod
    def _is_contextual_id_path(path: tuple[str | int, ...]) -> bool:
        if len(path) == 4 and path[0:2] == ("sse", "events") and path[3] == "id":
            return isinstance(path[2], int)
        if len(path) == 4 and path[0:2] == ("conversation", "messages") and path[3] == "id":
            return isinstance(path[2], int)
        return path == ("conversation", "session", "id")

    def _register_times(self, value: Any) -> None:
        discovered: set[str] = set()

        def visit(item: Any, path: tuple[str | int, ...] = ()) -> None:
            if isinstance(item, Mapping):
                for child_key, child in item.items():
                    visit(child, (*path, child_key))
                return
            if isinstance(item, list):
                for index, child in enumerate(item):
                    visit(child, (*path, index))
                return
            if (
                isinstance(item, str)
                and self._strategy(
                    path, path[-1] if path and isinstance(path[-1], str) else None
                )
                == "monotonic_time_map"
            ):
                discovered.add(item)

        visit(value)
        for timestamp in sorted(discovered):
            if timestamp not in self._times:
                ordinal = len(self._times) + 1
                if self._rfc3339_timestamps:
                    normalized = datetime(2000, 1, 1, tzinfo=UTC) + timedelta(
                        milliseconds=ordinal
                    )
                    self._times[timestamp] = normalized.isoformat(
                        timespec="milliseconds"
                    ).replace("+00:00", "Z")
                else:
                    self._times[timestamp] = f"<time:{ordinal:04d}>"

    @staticmethod
    def _stable_map(mapping: dict[str, str], value: str, label: str) -> str:
        existing = mapping.get(value)
        if existing is not None:
            return existing
        normalized = f"<{label}:{len(mapping) + 1:04d}>"
        mapping[value] = normalized
        return normalized


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def resolve_json_pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ValueError(f"JSON pointer must start with '/': {pointer!r}")

    current = document
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if token not in current:
                raise AssertionError(f"missing JSON pointer token {token!r} in {pointer!r}")
            current = current[token]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
            if not token.isascii() or not token.isdecimal() or (
                len(token) > 1 and token.startswith("0")
            ):
                raise AssertionError(
                    f"invalid array token {token!r} in {pointer!r}"
                )
            index = int(token)
            try:
                current = current[index]
            except IndexError as exc:
                raise AssertionError(
                    f"array token {token!r} is out of range in {pointer!r}"
                ) from exc
        else:
            raise TypeError(f"cannot descend through scalar at token {token!r} in {pointer!r}")
    return current


def assert_json_join(
    documents: Mapping[str, Any],
    references: Sequence[tuple[str, str]],
) -> Any:
    if len(references) < 2:
        raise ValueError("a join assertion requires at least two references")

    resolved: list[tuple[str, str, Any]] = []
    for document_name, pointer in references:
        if document_name not in documents:
            raise AssertionError(f"unknown join document: {document_name!r}")
        resolved.append(
            (document_name, pointer, resolve_json_pointer(documents[document_name], pointer))
        )

    expected = resolved[0][2]
    mismatches = [item for item in resolved[1:] if item[2] != expected]
    if mismatches:
        details = ", ".join(
            f"{document_name}{pointer}={value!r}" for document_name, pointer, value in resolved
        )
        raise AssertionError(f"cross-plane join mismatch: {details}")
    return expected


def assert_contiguous_order(items: Sequence[Mapping[str, Any]], field: str) -> None:
    observed = [item.get(field) for item in items]
    expected = list(range(len(items)))
    if observed != expected:
        raise AssertionError(f"{field} must be contiguous from zero: {observed!r}")


def assert_monotonic_timestamps(
    items: Sequence[Mapping[str, Any]],
    field: str,
    *,
    allow_equal: bool = True,
) -> None:
    parsed: list[datetime] = []
    for item in items:
        raw = item.get(field)
        if not isinstance(raw, str):
            raise TypeError(f"{field} must be an ISO date-time string: {raw!r}")
        try:
            parsed.append(datetime.fromisoformat(raw))
        except ValueError as exc:
            raise AssertionError(f"invalid {field}: {raw!r}") from exc
    pairs = pairwise(parsed)
    if allow_equal:
        valid = all(left <= right for left, right in pairs)
    else:
        valid = all(left < right for left, right in pairs)
    if not valid:
        raise AssertionError(f"{field} must be monotonic: {parsed!r}")


def assert_duration_bounds(value: Any, *, minimum: float = 0, maximum: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"duration must be numeric: {value!r}")
    if not minimum <= value <= maximum:
        raise AssertionError(
            f"duration {value!r} is outside expected range [{minimum!r}, {maximum!r}]"
        )
