"""§15.3 固化质量评测 harness —— 移植自 bench/eval-consolidation.mjs(P2-10)。

三级流程(每场景):run_scenario(真跑写路径)→ check_structural(硬判、程序)→ score_gists(软判、LLM judge)。
**判定纯函数(check_structural / parse_yes_no)可逐位对拍**(shared/parity/eval-checks.json);
run_scenario / score_gists 需真 LLM,跨语言只能比 §15.3 分布(非逐位),且最终终审仍是 dogfood。

跨语言可比纪律:必须与 TS 用【同一份 corpus、同一 judge 端点、逐字同 JUDGE_PROMPT、同 JUDGE_RUNS、同 GIST_SCORING_VERSION】;
产出同 schema 的 run JSON 后,直接用 TS 的 `node bench/eval-consolidation.mjs --compare a.json b.json` 对分(纯离线)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ._jsstr import js_trim
from .clock import to_iso_z
from .config import CONFIG, Config
from .llm.client import ChatMessage, LLMClient
from .store import open_db
from .store.cognition import SqliteCognitionStore
from .store.event import SqliteEventStore
from .store.evidence import SqliteEvidenceStore
from .store.semantic_resolution import SqliteSemanticResolutionStore
from .types import CognitionInput, EvidenceInput, Lang
from .update_profile import update_profile

#: judge 每个要点跑几次取多数(温度 0,防真模型抖动)。对齐 eval-consolidation.mjs:52。
JUDGE_RUNS = 3
#: gist 评分【口径】版本(v2:conflict 的 shouldForm 走确定性硬判)。对齐 :60。
GIST_SCORING_VERSION = "v2"

#: judge 判分提示词(**逐字对齐 TS**,否则软分跨语言不可比)。对齐 :67-87。
JUDGE_PROMPT_VERSION = "v1"
_JUDGE_SYSTEM: dict[str, str] = {
    "zh": "你是严格的语义匹配判官。只回答一个词：YES 或 NO。不要解释、不要任何多余文字。",
    "en": "You are a strict semantic-match judge. Answer with exactly one word: YES or NO. No explanation, no extra text.",
}


def _render_cognition_list(contents: list[str], lang: Lang) -> str:
    if not contents:
        return "（无，没有形成任何认知）" if lang == "zh" else "(none, no cognition was formed)"
    return "\n".join(f"{i + 1}. {c}" for i, c in enumerate(contents))


def judge_form_question(contents: list[str], gist: str, lang: Lang) -> str:
    listing = _render_cognition_list(contents, lang)
    if lang == "zh":
        return f"已形成的认知如下：\n{listing}\n\n其中是否有一条在语义上匹配这个要点：『{gist}』？只答 YES 或 NO。"
    return f'The formed cognitions are:\n{listing}\n\nIs there one among them that semantically matches this point: "{gist}"? Answer only YES or NO.'


def judge_not_question(contents: list[str], gist: str, lang: Lang) -> str:
    listing = _render_cognition_list(contents, lang)
    if lang == "zh":
        return f"已形成的认知如下：\n{listing}\n\n其中是否有一条断言了『{gist}』（这属于过度推断）？只答 YES 或 NO。"
    return f'The formed cognitions are:\n{listing}\n\nDoes any one of them assert "{gist}" (which would be an over-inference)? Answer only YES or NO.'


_YES_RE = re.compile(r"\bYES\b")
_NO_RE = re.compile(r"\bNO\b")


def parse_yes_no(ans: Any) -> bool:
    """解析 judge 的 YES/NO(容错:大小写/标点/夹句中;含糊 → 保守判 NO)。对齐 :99-109。"""
    t = js_trim(str(ans)).upper()
    ym = _YES_RE.search(t)
    nm = _NO_RE.search(t)
    yi = ym.start() if ym is not None else -1
    ni = nm.start() if nm is not None else -1
    has_yes = yi >= 0
    has_no = ni >= 0
    if has_yes and not has_no:
        return True
    if has_no and not has_yes:
        return False
    if has_yes and has_no:
        return yi < ni  # 两个都出现 → 取先出现的
    return False  # 都没有 → 保守判 NO


@dataclass(slots=True)
class Check:
    name: str
    passed: bool
    detail: str


def check_structural(scenario: dict[str, Any], run: dict[str, Any]) -> list[Check]:
    """结构性断言(程序判、不调 LLM)。逐位对拍 eval-consolidation.mjs:319-394。

    run["evidenceIds"] 接受 list 或 set(JSON 夹具里是 list)。
    """
    if run.get("error"):
        return [Check("run", False, f"updateProfile 抛错: {run['error']}")]
    c = run["consolidated"]
    ex = scenario.get("expect") or {}
    checks: list[Check] = []

    if ex.get("conflict"):
        checks.append(Check("conflicted≥1", c["conflicted"] >= 1, f"conflicted={c['conflicted']}"))
    if ex.get("correct"):
        checks.append(Check("corrected≥1", c["corrected"] >= 1, f"corrected={c['corrected']}"))
    nc = ex.get("newCognitions")
    if nc:
        mn, mx = nc["min"], nc["max"]
        checks.append(Check(f"created∈[{mn},{mx}]", mn <= c["createdCount"] <= mx, f"created={c['createdCount']}"))
        types = nc.get("types")
        if types:
            allow = set(types)
            bad = list(dict.fromkeys(x["contentType"] for x in c["created"] if x["contentType"] not in allow))
            checks.append(
                Check(f"created类型⊆{{{','.join(types)}}}", len(bad) == 0, f"越界类型: {','.join(bad)}" if bad else "ok")
            )
        formed_by = nc.get("formedBy")
        if formed_by:
            allow = set(formed_by)
            bad = list(dict.fromkeys(x["formedBy"] for x in c["created"] if x["formedBy"] not in allow))
            checks.append(
                Check(f"created来源⊆{{{','.join(formed_by)}}}", len(bad) == 0, f"越界来源: {','.join(bad)}" if bad else "ok")
            )
    if scenario.get("discipline") == "chitchat-negative":
        checks.append(Check("chitchat→created===0", c["createdCount"] == 0, f"created={c['createdCount']}"))
    if scenario.get("discipline") == "short-reply":
        need = [x for x in run["resolutions"] if x["hasAiContext"] and x["sourceKind"] == "spoken"]
        missing = [x for x in need if not x["res"]]
        if len(need) == 0:
            detail = "语料无带AI上文的spoken原话（CORP-20 本应拦住）"
        elif missing:
            detail = f"{len(missing)}/{len(need)} 条缺解析"
        else:
            detail = f"{len(need)}/{len(need)} 条有解析"
        checks.append(Check("带AI上文的原话都落了解析", len(need) > 0 and len(missing) == 0, detail))
        ra = (ex.get("resolutions") or {}).get("responseAct")
        if ra:
            allow = set(ra)
            acts = [x["res"]["responseAct"] for x in need if x["res"] is not None and x["res"].get("responseAct") is not None]
            bad = list(dict.fromkeys(a for a in acts if a not in allow))
            if len(acts) == 0:
                detail = "无解析可判（①已判红）"
            elif bad:
                detail = f"越界: {','.join(bad)}"
            else:
                detail = ",".join(acts)
            checks.append(Check(f"resolution.responseAct⊆{{{','.join(ra)}}}", len(acts) > 0 and len(bad) == 0, detail))

    # 不变量①:confidence ∈ (0,1000]
    conf_bad = [a for a in run["active"] if not (a["confidence"] > 0 and a["confidence"] <= 1000)]
    checks.append(
        Check(
            "不变量·confidence∈(0,1000]", len(conf_bad) == 0,
            f"越界: {','.join(str(a['confidence']) for a in conf_bad)}" if conf_bad else f"{len(run['active'])}条active合规",
        )
    )
    # 不变量②:state 封顶
    state_bad = [a for a in run["active"] if a["contentType"] == "state" and a["credStatus"] not in ("candidate", "low")]
    checks.append(
        Check(
            "不变量·state封顶∈{candidate,low}", len(state_bad) == 0,
            f"越界档: {','.join(a['credStatus'] for a in state_bad)}" if state_bad else "ok",
        )
    )
    # 不变量③:证据链引用真实存在
    evidence_ids = set(run["evidenceIds"])
    chain_bad = [s["evidenceId"] for cs in run["cogSources"] for s in cs["sources"] if s["evidenceId"] not in evidence_ids]
    checks.append(
        Check("不变量·证据链引用真实存在", len(chain_bad) == 0, f"虚构evidenceId {len(chain_bad)}个" if chain_bad else "ok")
    )
    return checks


def judge_majority(judge: LLMClient, lang: Lang, question: str) -> tuple[list[bool], bool]:
    """judge 投 JUDGE_RUNS 票(温度 0 由 judge 实例保证),取严格多数。对齐 :131-142。"""
    votes: list[bool] = []
    for _ in range(JUDGE_RUNS):
        ans = judge.chat(
            [
                ChatMessage(role="system", content=_JUDGE_SYSTEM.get(lang, _JUDGE_SYSTEM["en"])),
                ChatMessage(role="user", content=question),
            ]
        )
        votes.append(parse_yes_no(ans))
    yes_count = sum(1 for v in votes if v)
    return votes, yes_count * 2 > JUDGE_RUNS


@dataclass(slots=True)
class GistScores:
    form_results: list[dict[str, Any]]
    not_results: list[dict[str, Any]]
    gist_recall: Optional[float]
    over_infer_rate: Optional[float]


def score_gists(scenario: dict[str, Any], run: dict[str, Any], judge: LLMClient) -> GistScores:
    """shouldForm/shouldNot 逐条判分。conflict 的 shouldForm 走**确定性硬判**(GIST_SCORING_VERSION v2)。对齐 :160-192。"""
    contents = [c["content"] for c in run["active"]]
    lang: Lang = "zh" if scenario.get("lang") == "zh" else "en"
    ex = scenario.get("expect") or {}
    forms = ex.get("shouldFormGists") or []
    nots = ex.get("shouldNotFormGists") or []
    is_conflict = scenario.get("discipline") == "conflict"
    conflict_surfaced = any(c["credStatus"] == "conflicted" for c in run["active"])

    form_results: list[dict[str, Any]] = []
    for gist in forms:
        if is_conflict:
            form_results.append({"gist": gist, "hit": conflict_surfaced, "deterministic": True, "signal": "conflicted-status"})
        else:
            votes, yes = judge_majority(judge, lang, judge_form_question(contents, gist, lang))
            form_results.append({"gist": gist, "votes": votes, "hit": yes})
    not_results: list[dict[str, Any]] = []
    for gist in nots:
        votes, yes = judge_majority(judge, lang, judge_not_question(contents, gist, lang))
        not_results.append({"gist": gist, "votes": votes, "overInferred": yes})

    gist_recall = (sum(1 for r in form_results if r["hit"]) / len(forms)) if forms else None
    over_infer_rate = (sum(1 for r in not_results if r["overInferred"]) / len(nots)) if nots else None
    return GistScores(form_results=form_results, not_results=not_results, gist_recall=gist_recall, over_infer_rate=over_infer_rate)


class _NullRetriever:
    """评测不测召回:索引 no-op(对齐 TS 的 NullRetriever)。"""

    def index_all(self, items: list[tuple[str, str]]) -> None:
        return None


def run_scenario(scenario: dict[str, Any], llm: LLMClient, *, now: Optional[datetime] = None, cfg: Config = CONFIG) -> dict[str, Any]:
    """建 :memory: 四 store、预置 seed、按序喂 message、真跑 update_profile,收集产出快照。对齐 :204-301。

    证据一律显式 allow_cloud_read=True——被测多为云 tier,否则 observed 会被隐私门静默丢弃、评的就不是固化质量了。
    """
    lang: Lang = "zh" if scenario.get("lang") == "zh" else "en"
    base = (now if now is not None else datetime.now(timezone.utc)) - timedelta(hours=1)
    db = open_db(":memory:")
    try:
        ev = SqliteEvidenceStore(db, cfg)
        evt = SqliteEventStore(db)
        cog = SqliteCognitionStore(db)
        sem = SqliteSemanticResolutionStore(db)
        for s in scenario.get("seed") or []:
            cog.put(
                CognitionInput(
                    subject_id="owner", content=s["content"], content_type=s["contentType"], formed_by=s["formedBy"],
                    confidence=s["confidence"], cred_status=s["credStatus"],
                )
            )
        evidence_meta: list[dict[str, Any]] = []
        for i, m in enumerate(scenario["messages"]):
            e = ev.put(
                EvidenceInput(
                    subject_id="owner", source_kind=m["sourceKind"], host_id="local", raw_content=m["rawContent"],
                    preceding_ai_context=m.get("precedingAiContext"),
                    occurred_at=to_iso_z(base + timedelta(seconds=i)),
                    allow_cloud_read=True,
                )
            )
            evidence_meta.append(
                {"id": e.id, "sourceKind": m["sourceKind"], "hasAiContext": bool(js_trim(m.get("precedingAiContext") or ""))}
            )

        result = update_profile(
            "owner", evidence_store=ev, event_store=evt, cognition_store=cog, semantic_resolution_store=sem,
            retriever=_NullRetriever(), llm=llm, cfg=cfg, lang=lang,
        )
        active = [
            {"id": c.id, "content": c.content, "contentType": c.content_type, "credStatus": c.cred_status,
             "confidence": c.confidence, "formedBy": c.formed_by}
            for c in cog.active("owner")
        ]
        cog_sources = [
            {"id": c.id, "contentType": c.content_type,
             "sources": [{"evidenceId": s.evidence_id, "relation": s.relation} for s in cog.sources_of(c.id)]}
            for c in cog.all("owner")
        ]
        resolutions = []
        for x in evidence_meta:
            r = sem.of_evidence(x["id"])
            resolutions.append(
                {**x, "res": None if r is None else {
                    "resolvedContent": r.resolved_content, "responseAct": r.response_act, "promptAct": r.prompt_act,
                    "propositionOrigin": r.proposition_origin, "assertionStrength": r.assertion_strength,
                }}
            )
        return {
            "error": None,
            "consolidated": {
                "created": [
                    {"content": c.content, "contentType": c.content_type, "credStatus": c.cred_status,
                     "confidence": c.confidence, "formedBy": c.formed_by}
                    for c in result.consolidated.created
                ],
                "createdCount": len(result.consolidated.created),
                "reinforced": result.consolidated.reinforced,
                "corrected": result.consolidated.corrected,
                "conflicted": result.consolidated.conflicted,
                "processedEvents": result.consolidated.processed_events,
            },
            "active": active,
            "cogSources": cog_sources,
            "evidenceIds": [e.id for e in ev.all()],
            "resolutions": resolutions,
            "timings": {
                "distillMs": result.timings.distill_ms, "consolidateMs": result.timings.consolidate_ms,
                "attributeMs": result.timings.attribute_ms, "indexMs": result.timings.index_ms,
                "totalMs": result.timings.total_ms,
            },
        }
    except Exception as e:
        return {"error": str(e), "consolidated": None, "active": [], "cogSources": [], "evidenceIds": [], "resolutions": [], "timings": None}
    finally:
        db.close()
