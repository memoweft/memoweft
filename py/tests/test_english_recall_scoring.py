"""G9 item 4: stopped-exam lexical failures and synthetic precision controls.

English quotations below are fictional exam data, not personal memory. Chinese
counterparts are newly authored equivalents. No model or exam runner is used.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Any, Literal

import pytest

from memoweft.integrations.hermes.recall import (
    _match_world_rows, match_cognitions, recall_world_snapshot,
)
from memoweft.store import open_db
from test_semantic_recall import fixture
from test_trust_command_service import _command, _seed_cognition, _seed_revision, _service


CASES = [
    # reports/reproduction-fixtures.json: session11/question0.
    ("travel", "What alternatives is Martin exploring to wellness retreats as of October 16, 2026?",
     "I am actually looking into alternatives like nature hikes and cultural tours.",
     "I think their adaptability is what really drew me to them.",
     "我在考虑哪些替代选择？", "我正在考虑自然徒步和文化游等替代选择。", "它们的适应能力吸引了我。"),
    # qa-audit.json and session 004/after.json: session5/question0.
    ("pets", "Did Martin express a preference for Labradors over Golden Retrievers on Jan 06, 2026?",
     "I've always valued adaptability in pets, and Golden Retrievers seem to embody that trait well.",
     "My name is Martin Mark, and I am a Male.",
     "我喜欢拉布拉多还是金毛寻回犬？", "我一直重视宠物的适应能力，金毛寻回犬体现了这个特点。", "我叫林晓，是男性。"),
    # qa-audit.json and session 009/after.json: session10/question2.
    ("games", "Did Martin express interest in violent video games on July 14, 2026?",
     "I've always avoided violent video games because they don't align with my values.",
     "My Father was born on 1963-08-02, Retired doctor who inspired Martin's interest in health.",
     "我对暴力电子游戏有什么看法？", "我一直避开暴力电子游戏，因为它们不符合我的价值观。", "我的父亲是退休医生，启发了我对健康的兴趣。"),
]


def _rows(target: str, distractor: str) -> list[dict[str, Any]]:
    return [{"kind": "cognition", "id": mid, "content": text, "confidence": 600}
            for mid, text in [("target", target), ("distractor", distractor)]]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c[0])
@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("matcher", [_match_world_rows, match_cognitions])
def test_exam_topic_ranks_first(case: tuple[str, ...], language: str, matcher: Any) -> None:
    _, en_query, en_target, en_distractor, zh_query, zh_target, zh_distractor = case
    query, target, distractor = ((en_query, en_target, en_distractor) if language == "en"
                                  else (zh_query, zh_target, zh_distractor))
    hits = matcher(query, _rows(target, distractor))
    assert hits and hits[0]["id"] == "target", hits


@pytest.mark.parametrize("case", CASES, ids=lambda c: c[0])
@pytest.mark.parametrize("language", ["en", "zh"])
def test_production_snapshot_keeps_verbatim_body_and_is_read_only(
    tmp_path: Path, case: tuple[str, ...], language: Literal["en", "zh"],
) -> None:
    _, en_query, en_target, en_distractor, zh_query, zh_target, zh_distractor = case
    query, target, distractor = ((en_query, en_target, en_distractor) if language == "en"
                                  else (zh_query, zh_target, zh_distractor))
    path = tmp_path / "world.sqlite3"
    fixture.seed(path, {"memories": _rows(target, distractor)})
    with closing(open_db(str(path))) as db:
        before = list(db.iterdump())
        result = recall_world_snapshot(db, "fictional-owner", query, lang=language)
        assert result is not None and result.selected_item_ids[0] == ("cognition", "target")
        assert target in result.rendered_recall
        for _ in range(3):
            assert recall_world_snapshot(db, "fictional-owner", query, lang=language) == result
        assert list(db.iterdump()) == before


@pytest.mark.parametrize("query,content", [
    ("What alternatives are there to commuter trains?", "I think their adaptability is what drew me to them."),
    ("What do I know?", "I know what I like and I think it is what they like."),
    ("What telescope do I use?", "I use a microscope."),
    ("Which cats do I like?", "I like catering."),
    ("Which running shoes do I use?", "I use a database and enjoy running queries."),
    ("What is my middle name?", "My gardening tools are in the middle drawer."),
    ("I'm learning pottery, and I'm considering sculpture.", "I'm cataloguing nebulae."),
    ("I'll study pottery, and I'll consider sculpture.", "I'll catalogue nebulae."),
    ("I'd study pottery, and I'd consider sculpture.", "I'd catalogue nebulae."),
    ("我的望远镜是什么？", "我喜欢显微镜。"),
])
def test_unrelated_and_partial_words_do_not_supply_an_answer(query: str, content: str) -> None:
    rows = [{"kind": "cognition", "id": "unrelated", "content": content, "confidence": 600}]
    assert _match_world_rows(query, rows) == []
    assert match_cognitions(query, rows) == []


@pytest.mark.parametrize("case", CASES, ids=lambda c: c[0])
def test_case_normalization_preserves_rank_and_score(case: tuple[str, ...]) -> None:
    _, query, target, distractor, *_ = case
    original = _match_world_rows(query, _rows(target, distractor))
    changed = _match_world_rows(query.upper(), _rows(target.upper(), distractor.upper()))
    assert [(hit["id"], hit["score"]) for hit in changed] == [(hit["id"], hit["score"]) for hit in original]


@pytest.mark.parametrize("query", ["Which running shoes do I use?", "Which running shoes have I used?"])
def test_topic_nomination_cannot_bypass_english_topic_weights(query: str) -> None:
    rows = [{"kind": "cognition", "id": "unrelated", "confidence": 600,
             "content": "I use a database for running queries.", "topic_cues": ("running",)}]
    assert _match_world_rows(query, rows) == []


def test_partial_requested_phrase_can_recall_with_corroborating_topic_words() -> None:
    rows = [{"kind": "cognition", "id": "target", "confidence": 600,
             "content": "I wear hiking boots during winter."}]
    # No 'comfortable', 'specific' or 'current' in the source. Neither an
    # unrecognized modifier nor an auxiliary can become a mandatory word.
    hits = _match_world_rows("Which specific comfortable winter hiking boots have I used?", rows)
    assert hits and hits[0]["id"] == "target"


@pytest.mark.parametrize("state", ["other_account", "cross_evidence", "forgotten", "local_denied", "cloud_denied"])
@pytest.mark.parametrize("language", ["en", "zh"])
def test_improved_match_cannot_bypass_source_gates(tmp_path: Path, state: str, language: str) -> None:
    path = tmp_path / "blocked.sqlite3"
    query, text = (CASES[0][1], CASES[0][2]) if language == "en" else (CASES[0][4], CASES[0][5])
    fixture.seed(path, {"memories": [{"id": "blocked", "content": text, "state": state}]})
    with closing(open_db(str(path))) as db:
        result = recall_world_snapshot(db, "fictional-owner", query,
                                       model_tier="cloud" if state == "cloud_denied" else "local")
        assert result is not None and result.selected_item_ids == ()


def test_assistant_context_from_another_conversation_is_not_searchable(tmp_path: Path) -> None:
    path = tmp_path / "context.sqlite3"
    fixture.seed(path, {"memories": [{"id": "user", "content": "I collect ceramic bowls."}]})
    with closing(open_db(str(path))) as db:
        db.execute("UPDATE evidence SET preceding_ai_context=?", (CASES[0][2],))
        db.execute("INSERT INTO interaction_context(id,subject_id,conversation_id,episode_id,context_hash,"
                   "context_json,created_at) VALUES('assistant','fictional-owner','other-session','episode','hash',?, '2026-01-01')",
                   ('[{"role":"assistant","content":"I recommend nature hikes and cultural tours."}]',))
        db.commit()
        result = recall_world_snapshot(db, "fictional-owner", CASES[0][1])
        assert result is not None and result.selected_item_ids == ()


def test_other_session_unformed_user_quote_is_not_a_world_match(tmp_path: Path) -> None:
    path = tmp_path / "unformed.sqlite3"
    fixture.seed(path, {"memories": [{"id": "user", "content": "I collect ceramic bowls."}]})
    with closing(open_db(str(path))) as db:
        db.execute("INSERT INTO evidence(id,subject_id,source_kind,host_id,origin_id,occurred_at,recorded_at,"
                   "raw_content,summary,allow_local_read,allow_cloud_read,allow_inference) "
                   "VALUES('unformed','fictional-owner','spoken','synthetic','other-session:turn',"
                   "'2026-01-01','2026-01-01',?,?,1,1,1)", (CASES[0][2], CASES[0][2]))
        db.commit()
        result = recall_world_snapshot(db, "fictional-owner", CASES[0][1])
        assert result is not None and result.selected_item_ids == ()


@pytest.mark.parametrize("language", ["en", "zh"])
def test_actual_forget_receipt_prevents_recall_of_previously_matching_fact(tmp_path: Path, language: str) -> None:
    path = tmp_path / "forget.sqlite3"
    query, text = (CASES[0][1], CASES[0][2]) if language == "en" else (CASES[0][4], CASES[0][5])
    with closing(open_db(str(path))) as db:
        _seed_cognition(db, content=text)
        _seed_revision(db)
        db.commit()
        before = recall_world_snapshot(db, "owner", query)
        assert before is not None and before.selected_item_ids == (("cognition", "cog-coffee"),)
    receipt = _service(path).submit_command(_command("forget", 1, "forget_evidence", "evidence", "e-coffee"))
    assert receipt["result_state"] == "applied"
    with closing(open_db(str(path))) as db:
        after = recall_world_snapshot(db, "owner", query)
        assert after is not None and after.selected_item_ids == ()
