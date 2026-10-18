"""Main substantive candidates survive independently of scores and thresholds."""
import json
from typing import Any, cast

import pytest

from memoweft.integrations.hermes import recall
from support.english_recall_gate import HERE
from support.english_invariant_spec import check

RANDOM = json.loads((HERE / "english-invariant-random.json").read_text(encoding="utf-8"))["cases"]
LABELLED = json.loads((HERE / "english-invariant-labelled.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("surface", ["world", "cognition"])
@pytest.mark.parametrize("group", [RANDOM, LABELLED], ids=["1152-seeded-random", "all-english-collections"])
def test_every_dropped_main_candidate_is_function_only(surface: str, group: list[dict[str, Any]]) -> None:
    matcher = recall._match_world_rows if surface == "world" else recall.match_cognitions
    for case in group:
        result = matcher(case["query"], case["rows"])
        check(case, cast(list[dict[str, Any]], result), surface)
