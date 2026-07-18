"""extract_json_object + parse_json_object parity:Python 与 TS(shared/parity/json-extract.json)逐例一致。

验证括号配平（跳字符串内花括号/转义）+ 去围栏 + 只认对象 + JSON.parse 拒 NaN/Infinity（parse_constant）。
"""
from __future__ import annotations

from typing import Any

from conftest import parity

from memoweft.llm.json_repair import extract_json_object, parse_json_object


def test_extract_json_object_matches_ts() -> None:
    data: Any = parity("json-extract.json")
    for case in data["extractJsonObject"]["cases"]:
        assert extract_json_object(case["input"]) == case["expected"], f"extract {case['input']!r}"


def test_parse_json_object_matches_ts() -> None:
    data: Any = parity("json-extract.json")
    for case in data["parseJsonObject"]["cases"]:
        assert parse_json_object(case["input"]) == case["expected"], f"parse {case['input']!r}"
