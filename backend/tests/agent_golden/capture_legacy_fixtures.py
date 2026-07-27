from __future__ import annotations

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from _pytest.monkeypatch import MonkeyPatch

from agent_golden.contract_validation import expected_fixture_matrix
from agent_golden.fixture_writer import capture_legacy_envelopes, write_envelopes
from agent_golden.harness import GoldenHarness
from agent_golden.legacy_scenario_capture import plan_for_variant
from agent_golden.support import load_json

CAPTURED_VARIANTS = (
    "GT01-sync",
    "GT01-sse",
    "GT02-ask-refresh-continue",
    "GT13-llm-error",
    "GT15-full",
    "GT16-history",
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--update",
        action="store_true",
        help="Replace checked-in fixtures. Without this flag, only check for drift.",
    )
    parser.add_argument(
        "--variant",
        choices=CAPTURED_VARIANTS,
        action="append",
        help="Capture only the selected variant. May be repeated.",
    )
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[3]
    contract_root = repo_root / "contracts" / "agent" / "v1"
    matrix = expected_fixture_matrix(contract_root)
    with TemporaryDirectory() as directory:
        temp_root = Path(directory)
        for variant_id in args.variant or CAPTURED_VARIANTS:
            monkeypatch = MonkeyPatch()
            try:
                paths = {
                    item.plane: item.path
                    for item in matrix.values()
                    if item.fixture_set == "legacy_characterization"
                    and item.variant_id == variant_id
                }
                captured_revision = None
                if not args.update and all(path.is_file() for path in paths.values()):
                    revisions = {
                        load_json(path)["source"]["repo_revision"]
                        for path in paths.values()
                    }
                    if len(revisions) != 1:
                        raise AssertionError(
                            f"fixture provenance revisions disagree for {variant_id}"
                        )
                    captured_revision = revisions.pop()
                harness = GoldenHarness(
                    temp_root / f"{variant_id}.sqlite3",
                    monkeypatch,
                    plan_for_variant(variant_id),
                )
                try:
                    envelopes = capture_legacy_envelopes(
                        contract_root,
                        harness,
                        variant_id,
                        monkeypatch,
                        captured_revision=captured_revision,
                    )
                finally:
                    harness.close()
                if args.update:
                    write_envelopes(envelopes, paths)
                    continue
                mismatches = [
                    path
                    for plane, path in paths.items()
                    if not path.is_file() or load_json(path) != envelopes[plane]
                ]
                if mismatches:
                    relative = [path.relative_to(repo_root).as_posix() for path in mismatches]
                    raise AssertionError(
                        "legacy fixture drift detected; inspect the behavior change before "
                        f"running --update: {relative}"
                    )
            finally:
                monkeypatch.undo()


if __name__ == "__main__":
    main()
