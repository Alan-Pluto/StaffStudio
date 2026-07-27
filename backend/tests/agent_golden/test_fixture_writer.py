from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_golden.capture_legacy_fixtures import CAPTURED_VARIANTS
from agent_golden.contract_validation import expected_fixture_matrix
from agent_golden.fixture_writer import capture_legacy_envelopes
from agent_golden.harness import GoldenHarness
from agent_golden.legacy_scenario_capture import plan_for_variant
from agent_golden.support import load_json

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_ROOT = REPO_ROOT / "contracts" / "agent" / "v1"


@pytest.mark.parametrize("variant_id", CAPTURED_VARIANTS)
def test_runtime_recapture_matches_checked_in_fixtures(
    variant_id: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {
        item.plane: load_json(item.path)
        for item in expected_fixture_matrix(CONTRACT_ROOT).values()
        if item.fixture_set == "legacy_characterization"
        and item.variant_id == variant_id
    }
    captured_revisions = {
        envelope["source"]["repo_revision"] for envelope in expected.values()
    }
    assert len(captured_revisions) == 1
    harness = GoldenHarness(
        tmp_path / f"recapture-{variant_id}.sqlite3",
        monkeypatch,
        plan_for_variant(variant_id),
    )
    try:
        actual = capture_legacy_envelopes(
            CONTRACT_ROOT,
            harness,
            variant_id,
            monkeypatch,
            captured_revision=captured_revisions.pop(),
        )
    finally:
        harness.close()

    assert actual == expected


@pytest.mark.parametrize(
    ("hash_seed", "timezone"),
    [("1", "UTC"), ("777", "America/New_York")],
)
@pytest.mark.parametrize("variant_id", CAPTURED_VARIANTS)
def test_recapture_is_stable_in_fresh_processes(
    hash_seed: str,
    timezone: str,
    variant_id: str,
) -> None:
    env = {
        **os.environ,
        "PYTHONHASHSEED": hash_seed,
        "PYTHONPATH": str(REPO_ROOT / "backend" / "tests"),
        "TZ": timezone,
    }
    subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_golden.capture_legacy_fixtures",
            "--variant",
            variant_id,
        ],
        cwd=REPO_ROOT / "backend",
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
