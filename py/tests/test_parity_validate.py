"""validate_bundle parity:跨 Portable 版本保持结构与非版本文案逐字一致。"""
from __future__ import annotations

import copy
from typing import Any

from conftest import parity

from memoweft.portable import BUNDLE_SCHEMA_VERSION, validate_bundle


def _python_version_expected(
    case: dict[str, Any], *, ts_schema_version: int
) -> dict[str, Any]:
    """Translate only the current-schema number in the two version messages."""
    expected: dict[str, Any] = copy.deepcopy(case["expected"])
    if case["label"] == "schemaVersion-too-high":
        ts_message = (
            f"schemaVersion=99 is higher than the {ts_schema_version} supported by this "
            "version (upgrade MemoWeft before importing)"
        )
        assert expected["errors"] == [ts_message]
        expected["errors"] = [
            f"schemaVersion=99 is higher than the {BUNDLE_SCHEMA_VERSION} supported by "
            "this version (upgrade MemoWeft before importing)"
        ]
    elif case["label"] == "schemaVersion-lower":
        ts_message = (
            f"schemaVersion=1 is lower than the current {ts_schema_version} "
            "(importing with the old structure)"
        )
        assert ts_message in expected["warnings"]
        expected["warnings"] = [
            (
                f"schemaVersion=1 is lower than the current {BUNDLE_SCHEMA_VERSION} "
                "(importing with the old structure)"
                if warning == ts_message
                else warning
            )
            for warning in expected["warnings"]
        ]
    return expected


def test_validate_bundle_matches_ts() -> None:
    cases = parity("bundle-validate.json")["cases"]
    assert len(cases) >= 10
    valid_case = next(case for case in cases if case["label"] == "valid")
    ts_schema_version = valid_case["bundle"]["schemaVersion"]
    assert ts_schema_version == 4
    assert BUNDLE_SCHEMA_VERSION == 4

    for case in cases:
        got = validate_bundle(case["bundle"]).as_dict()
        expected = _python_version_expected(case, ts_schema_version=ts_schema_version)
        assert got == expected, (
            f"validateBundle 分叉 @ {case['label']}:\n got:  {got}\n want: {expected}"
        )
