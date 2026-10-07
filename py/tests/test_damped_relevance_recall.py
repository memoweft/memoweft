"""Tests for Context-Aware Damped Relevance Recall (Route 3).

Guarantees:
1. Long assertions / statements introducing new entities/topics do NOT trigger
   spurious recall via generic predicate overlap (e.g. '喜欢', 'like').
2. Inquiries and explicit topic cues accurately recall specific claims.
3. Overlapping bigrams consisting only of generic predicate/carrier words
   are damped to 0 score.
4. Pronoun expansion ('谁') maps to interpersonal entities, avoiding food/beverage cross-talk.
"""
from __future__ import annotations

from memoweft.integrations.hermes.recall import (
    _match_world_rows,
    match_cognitions,
)


def test_assertion_statement_does_not_spuriously_recall_unrelated_preferences() -> None:
    rows = [
        {"kind": "cognition", "id": "corn", "content": "用户喜欢吃玉米", "confidence": 600, "anchors": ()},
        {"kind": "cognition", "id": "rust", "content": "用户正在学习Rust", "confidence": 600, "anchors": ()},
        {"kind": "cognition", "id": "coffee", "content": "用户平时更喜欢冰美式。", "confidence": 600, "anchors": ()},
    ]

    # User introduces Wang Miss in a conversational statement
    query = "我和你说吧，王小姐是我喜欢的一个女生，特别好看又温柔"
    recalled = _match_world_rows(query, rows)
    assert recalled == [], f"Expected no spurious recall, got: {recalled}"

    cog_recalled = match_cognitions(query, rows)
    assert cog_recalled == [], f"Expected no spurious recall in match_cognitions, got: {cog_recalled}"


def test_english_assertion_does_not_spuriously_recall_like_preferences() -> None:
    rows = [
        {"kind": "cognition", "id": "corn", "content": "User likes eating corn", "confidence": 600, "anchors": ()},
        {"kind": "cognition", "id": "rust", "content": "User is learning Rust", "confidence": 600, "anchors": ()},
    ]

    query = "Let me tell you, Alice is someone I really like, very sweet and kind"
    assert _match_world_rows(query, rows) == []
    assert match_cognitions(query, rows) == []


def test_generic_predicate_only_query_returns_empty() -> None:
    rows = [
        {"kind": "cognition", "id": "corn", "content": "用户喜欢吃玉米", "confidence": 600, "anchors": ()},
        {"kind": "cognition", "id": "coffee", "content": "用户平时更喜欢冰美式。", "confidence": 600, "anchors": ()},
    ]

    assert match_cognitions("喜欢", rows) == []
    assert match_cognitions("like", rows) == []
    assert match_cognitions("用户", rows) == []


def test_inquiry_with_domain_specificity_recalls_accurately() -> None:
    rows = [
        {"kind": "cognition", "id": "corn", "content": "用户喜欢吃玉米", "confidence": 600, "anchors": ()},
        {"kind": "cognition", "id": "rust", "content": "用户正在学习Rust", "confidence": 600, "anchors": ()},
        {"kind": "cognition", "id": "coffee", "content": "用户平时更喜欢冰美式。", "confidence": 600, "anchors": ()},
    ]

    # Inquiries
    assert [x["id"] for x in match_cognitions("我喜欢吃什么？", rows)] == ["corn"]
    assert [x["id"] for x in match_cognitions("用户喜欢什么饮料", rows)] == ["coffee"]
    assert [x["id"] for x in match_cognitions("用户在学什么编程语言？", rows)] == ["rust"]

    # Short exact topic cues
    assert [x["id"] for x in match_cognitions("玉米", rows)] == ["corn"]
    assert [x["id"] for x in match_cognitions("Rust", rows)] == ["rust"]


def test_named_entity_assertion_recalls_relevant_entity_only() -> None:
    rows = [
        {"kind": "cognition", "id": "corn", "content": "用户喜欢吃玉米", "confidence": 600, "anchors": ()},
        {"kind": "cognition", "id": "wang_milk_tea", "content": "王小姐：她特别喜欢喝奶茶", "confidence": 600, "anchors": ("王小姐",)},
    ]

    query = "我和王小姐今天去商场逛街了"
    recalled = _match_world_rows(query, rows)
    assert [x["id"] for x in recalled] == ["wang_milk_tea"]


def test_inquiry_who_expands_to_person_not_food() -> None:
    rows = [
        {"kind": "cognition", "id": "corn", "content": "用户喜欢吃玉米", "confidence": 600, "anchors": ()},
        {"kind": "cognition", "id": "zhang", "content": "用户喜欢的女生是张小姐", "confidence": 600, "anchors": ("张小姐",)},
    ]

    query = "我喜欢谁？"
    recalled = match_cognitions(query, rows)
    assert [x["id"] for x in recalled] == ["zhang"]
