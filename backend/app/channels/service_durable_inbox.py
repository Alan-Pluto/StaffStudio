from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class StageDisposition(StrEnum):
    STAGED = "staged"
    DUPLICATE = "duplicate"
    SECURITY_DROP = "security_drop"
    NACK = "nack"


@dataclass(frozen=True)
class StageResult:
    disposition: StageDisposition
    event_pk: str | None = None
    error_code: str | None = None

    @property
    def should_ack(self) -> bool:
        return self.disposition is not StageDisposition.NACK
