"""Behavioral precision boundaries for model-free paraphrase recall."""
from __future__ import annotations

from typing import Any

import pytest

from memoweft.integrations.hermes.recall import _match_world_rows
from memoweft.integrations.hermes.recall_terms import normalize, topics


def rows(*contents: str) -> list[dict[str, Any]]:
    return [{"kind": "cognition", "id": str(i), "content": content,
             "confidence": 600, "anchors": ()} for i, content in enumerate(contents)]


@pytest.mark.parametrize("query,content", [
    ("单车路线怎么选？", "我周末常去骑行。"),
    ("ＭＥＥＴＩＮＧ选哪天？", "我每周一开会。"),
    ("zixingche怎么安排？", "我骑自行车只走平路。"),
    ("自行車怎麼選？", "我喜欢骑行。"),
    ("宠物用品怎么选？", "我养了一只狗。"),
    ("中餐怎么点？", "我喜欢川菜。"),
    ("睡不着有什么建议？", "我最近入睡困难。"),
])
def test_paraphrases_select_exact_claim_without_rewriting(query: str, content: str) -> None:
    memories = rows(content, "我收藏虚构紫色邮票。")
    selected = _match_world_rows(query, memories)
    assert [item["id"] for item in selected] == ["0"]
    assert selected[0]["content"] == content
    assert memories == rows(content, "我收藏虚构紫色邮票。")


@pytest.mark.parametrize("query,content", [
    ("苹果手机怎么选？", "我喜欢吃苹果。"),
    ("苹果水果怎么吃？", "我使用苹果电脑。"),
    ("python snake怎么喂？", "我喜欢Python编程。"),
    ("花生壳域名怎么配置？", "我对花生过敏。"),
    ("狗粮怎么选？", "我喜欢狗尾草。"),
    ("猫眼石怎么收藏？", "我养了一只猫。"),
    ("调用meetingly怎么设置？", "我每周开会。"),
    ("supplier工具怎么设置？", "我研究UI设计。"),
    ("狗应该怎么喂？", "我有一只宠物。"),
    ("泰国菜怎么点？", "我喜欢花生。"),
    ("火锅怎么选？", "我喜欢川菜。"),
    ("怎么写小说？", "我容易疲劳。"),
])
def test_unknown_or_ambiguous_words_do_not_invent_relevance(query: str, content: str) -> None:
    assert _match_world_rows(query, rows(content)) == []


def test_indirect_dining_query_requires_an_existing_constraint() -> None:
    memories = rows("我对花生过敏。", "我喜欢花生。", "我的花生罐是蓝色。")
    assert [item["id"] for item in _match_world_rows("帮我点个泰国菜？", memories)] == ["0"]


def test_same_topic_competitors_use_requested_detail_and_stable_order() -> None:
    memories = rows("我的骑行头盔是蓝色。", "我的骑行路线必须是平路。", "我的骑行水壶是红色。")
    first = _match_world_rows("单车路线坡度怎么选？", memories)
    assert [item["id"] for item in first] == ["1"]
    assert first == _match_world_rows("单车路线坡度怎么选？", reversed(memories))


def test_literal_distractor_does_not_suppress_equivalent_topic() -> None:
    memories = rows("我使用单车品牌的手机。", "我周末常去骑行。")
    selected = _match_world_rows("自行车怎么安排？", memories)
    assert "1" in [item["id"] for item in selected]


def test_unknown_detail_abstains_from_ambiguous_expansion_and_keeps_literal_hit() -> None:
    memories = rows("我做饭只用不粘锅。", "我做饭围裙是蓝色。", "我做饭时收听广播。")
    assert [item["id"] for item in _match_world_rows("烹饪锅具怎么选？", memories)] == []
    assert [item["id"] for item in _match_world_rows("做饭不粘锅怎么选？", memories)] == ["0"]


def test_multiple_claims_with_same_requested_detail_do_not_expand() -> None:
    memories = rows("我的骑行路线是平路。", "我的骑行路线是山路。")
    assert _match_world_rows("单车坡度怎么选？", memories) == []


def test_ambiguous_expansion_preserves_literal_candidates() -> None:
    from memoweft.integrations.hermes.recall import _semantic_world_rows

    memories = rows("我的骑行路线是平路。", "我的骑行路线是山路。", "我的骑行头盔是蓝色。")
    expanded, literal = _semantic_world_rows("骑行路线坡度怎么选？", [row for row in memories])
    assert expanded == []
    assert literal == memories


def test_permission_checked_predecessor_selects_only_successor_content() -> None:
    memories = rows("更正，现在只走平路。")
    memories[0]["predecessor_match_texts"] = ("我骑行时喜欢山路。",)
    selected = _match_world_rows("单车路线怎么选？", memories)
    assert [item["content"] for item in selected] == ["更正，现在只走平路。"]
    memories[0]["predecessor_match_texts"] = ()
    assert _match_world_rows("单车路线怎么选？", memories) == []


def test_static_vocabulary_cannot_resurrect_removed_durable_topic_labels() -> None:
    memories = rows("我乘船时喜欢靠窗。")
    memories[0]["has_topic_labels"] = True
    memories[0]["topic_cues"] = ()
    assert _match_world_rows("坐船？", memories) == []


def test_normalization_keeps_raw_data_out_of_global_caches() -> None:
    assert normalize("ＭＥＥＴＩＮＧ 自行車 pǎobù") == "meeting 自行车 paobu"
    assert topics("kaihui") == frozenset({"meeting"})
    assert topics("kaihuix") == frozenset()
