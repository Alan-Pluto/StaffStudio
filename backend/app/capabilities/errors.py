from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityErrorInfo:
    code: str
    message: str
    retryable: bool
    request_id: str | None = None
    provider_id: str | None = None
    extensions: dict[str, object] | None = None

    def to_payload(self) -> dict[str, object]:
        """Map Python ``code`` to the wire contract's ``error_code`` once."""
        if not self.request_id:
            raise ValueError("provider errors require request_id")
        extensions = self.extensions or {}
        for namespace, value in extensions.items():
            if (
                not re.fullmatch(r"[a-z][a-z0-9_]*", namespace)
                or namespace in {"core", "staffdeck"}
                or not isinstance(value, dict)
            ):
                raise ValueError(f"invalid provider error extension namespace: {namespace}")
        try:
            encoded = json.dumps(extensions, ensure_ascii=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("provider error extensions must be JSON") from exc
        if len(encoded.encode("utf-8")) > 64 * 1024:
            raise ValueError("provider error extensions exceed the 64 KiB limit")
        return {
            "error_code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "request_id": self.request_id,
            "provider_id": self.provider_id,
            "extensions": self.extensions or {},
        }


class CapabilityProviderError(RuntimeError):
    """Stable provider failure; callers must branch on code/retryable."""

    def __init__(self, info: CapabilityErrorInfo) -> None:
        super().__init__(info.message)
        self.info = info


__all__ = ["CapabilityErrorInfo", "CapabilityProviderError"]
