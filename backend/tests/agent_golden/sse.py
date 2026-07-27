from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SSEEvent:
    id: str | None
    event: str
    data: dict[str, Any]


def parse_sse_lines(lines: Iterable[str]) -> list[SSEEvent]:
    events: list[SSEEvent] = []
    event_id: str | None = None
    event_name = "message"
    data_lines: list[str] = []

    def flush() -> None:
        nonlocal event_id, event_name, data_lines
        if not data_lines and event_name == "message" and event_id is None:
            return
        raw_data = "\n".join(data_lines)
        decoded = json.loads(raw_data) if raw_data else {}
        if not isinstance(decoded, dict):
            raise TypeError(f"SSE data must decode to an object: {decoded!r}")
        events.append(SSEEvent(id=event_id, event=event_name, data=decoded))
        event_id = None
        event_name = "message"
        data_lines = []

    for raw_line in lines:
        line = raw_line.rstrip("\r\n")
        if not line:
            flush()
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "id":
            event_id = value
        elif field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value)
    flush()
    return events
