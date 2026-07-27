from __future__ import annotations

from collections.abc import Iterator

import pytest

from agent_golden.harness import GoldenHarness
from agent_golden.scripted_dependencies import ScriptedLLMPlan


@pytest.fixture
def golden_harness(tmp_path, monkeypatch) -> Iterator[GoldenHarness]:
    harness = GoldenHarness(tmp_path / "golden.sqlite3", monkeypatch, ScriptedLLMPlan())
    try:
        yield harness
    finally:
        harness.close()
