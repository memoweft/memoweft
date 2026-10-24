"""Bounded, read-only owner context and diverse exact source excerpts."""
from __future__ import annotations

import calendar
import math
import re
import sqlite3
from typing import Any, Sequence, cast

from ...types import Lang,ModelTier
from ...semantic.runtime import CandidateProvider
from ..trust.currentness import _all_evidence_current,linked_evidence,world_item_visible
from .recall_identity import _direct_self_fact,_names_self
from .recall_render import recall_dates

PROFILE_ITEMS=18
PROFILE_CHARS=1800
QUOTE_ITEMS=5
QUOTE_CHARS=2600


def _scope(db: sqlite3.Connection,subject_id: str,query: str,rows: list[dict[str,object]],tier: ModelTier) -> tuple[bool,tuple[str,...],tuple[str,...],bool]:
    from .recall import _anchor_is_explicit,_entity_names
    if any(_anchor_is_explicit(query.casefold(),str(a).casefold()) for row in rows
           for a in cast(Sequence[str],row.get('ambiguous_anchors',()))):
        return False,(),(),True
    names: dict[str,tuple[str,...]]={}
    own={str(i) for row in rows for i in cast(Sequence[str],row.get('owner_entity_ids',()))}
    for entity_id, in db.execute("SELECT id FROM entity WHERE world_id=? AND kind='person' AND invalid_at IS NULL",(subject_id,)):
        values=_entity_names(db,subject_id,str(entity_id),tier)
        if not values:continue
        names[str(entity_id)]=values
    owner_names=tuple(dict.fromkeys(str(n) for row in rows for n in cast(Sequence[str],row.get('owner_names',()))))
    others=tuple(dict.fromkeys(n for i,ns in names.items() if i not in own for n in ns
                              if _anchor_is_explicit(query.casefold(),n.casefold())))
    if others:return False,owner_names,others,False
    matches=any(_anchor_is_explicit(query.casefold(),n.casefold()) for n in owner_names)
    first_person=bool(re.search(r"\b(?:I|me|my|mine)\b|我|本人",query,re.I))
    return matches or first_person,owner_names,(),False


def _profile_rows(db: sqlite3.Connection,subject_id: str,rows: list[dict[str,object]]) -> list[dict[str,object]]:
    from .batch_adapter import owner_entity_id_for
    owner=owner_entity_id_for(subject_id)
    result=[]
    for row in rows:
        if row['kind']!='cognition' or row.get('is_superseded') or row.get('statement_kind') not in ('attribute','naming'):
            continue
        if row.get('statement_kind')=='naming':
            if any(_names_self(str(row['content']),str(n)) for n in cast(Sequence[str],row.get('anchors',()))):
                result.append(row)
            continue
        target=db.execute('SELECT ct.target_entity_id,ct.perspective_entity_id,e.kind FROM cognition_target ct LEFT JOIN entity e ON e.id=ct.target_entity_id AND e.world_id=? WHERE ct.cognition_id=?',(subject_id,str(row['id']))).fetchone()
        if target and (target[1] is not None or str(target[0])!=owner and str(target[2])!='topic'):
            continue
        direct=_direct_self_fact(db,subject_id,str(row['id']))
        # Possessive self descriptions remain verbatim attributed context;
        # they never establish a person's identity or add name anchors.
        claim=str(row['content'])
        possessive=False
        if (re.match(r'^My\s|^(?:用户|我|本人)的',claim,re.I)
                and not re.match(r'^My\s+.+?\s+(?:is|was|were|are)\s+born\b',claim,re.I)):
            for raw, in db.execute("SELECT e.raw_content FROM evidence e JOIN cognition_evidence l ON l.evidence_id=e.id WHERE l.cognition_id=? AND l.relation='support' AND e.subject_id=? AND e.source_kind='spoken'",(str(row['id']),subject_id)):
                text=str(raw).strip()
                possessive=possessive or text.startswith(claim) or bool(re.search(r'[.!?。！？\n]\s*'+re.escape(claim),text))
        if direct or possessive or target and str(target[0])==owner:
            result.append(row)
    # No topical vocabulary: short typed identity facts get stable room before
    # longer views. Original attribution, values and complete text stay intact.
    return sorted(result,key=lambda r:(r.get('statement_kind')!='naming',len(str(r['content'])),str(r['id'])))


def profile_context(db: sqlite3.Connection,subject_id: str,query: str,tier: ModelTier,rows: list[dict[str,object]],lang: Lang) -> tuple[list[dict[str,object]],str]:
    applies,_,_,ambiguous=_scope(db,subject_id,query,rows,tier)
    if not applies or ambiguous:return [],''
    eligible=[r for r in _profile_rows(db,subject_id,rows) if world_item_visible(db,subject_id,cast(Any,r['kind']),str(r['id']),surface='model_cloud' if tier=='cloud' else 'recall')]
    dates=recall_dates(db,subject_id,eligible,tier)
    header='User profile (current; original attribution):' if lang=='en' else '本人档案（当前；保留原话归属）：'
    lines=[header]
    selected=[]
    for row in eligible:
        line=dates[(str(row['kind']),str(row['id']))]+str(row.get('english_content',row['content']) if lang=='en' else row['content'])
        if len('\n'.join(lines))+1+len(line)>PROFILE_CHARS:continue
        selected.append(row);lines.append(line)
        if len(selected)==PROFILE_ITEMS:break
    return selected,'\n'.join(lines) if selected else ''


def _facets(query: str,owner_names: tuple[str,...]) -> tuple[str,...]:
    from .recall import _ENGLISH_CARRIERS
    names={part.casefold() for n in owner_names for part in n.split()}
    months={n.casefold() for n in (*calendar.month_name,*calendar.month_abbr) if n}
    tokens=list(dict.fromkeys(w for w in re.findall(r"[A-Za-z]{3,}",query.casefold())
                            if w not in _ENGLISH_CARRIERS and w not in names and w not in months))[:8]
    parts=[' '.join(tokens[i:i+2]) for i in range(0,len(tokens),2)]
    for run in re.findall(r'[\u3400-\u9fff]{4,}',query):
        parts.extend(run[i:i+6] for i in range(0,min(len(run),24),6) if len(run[i:i+6])>=2)
    return tuple(dict.fromkeys((query,*parts)))[:5]


def _windows(text: str) -> list[str]:
    # Preserve whole sentences where possible; long sentences use overlap.
    # Uniformly bound very long sources while retaining their final clauses.
    if len(text)<=420:return [text]
    windows: list[str]=[]
    start=0
    end=0
    for sentence in re.finditer(r'[^.!?。！？]+[.!?。！？]?',text):
        if sentence.end()-start>420 and end>start:
            windows.append(text[start:end]);start=end
        if sentence.end()-start>420:
            windows.extend(text[i:min(i+420,sentence.end())] for i in range(start,sentence.end(),360))
            start=sentence.end()
        end=sentence.end()
    if end>start:windows.append(text[start:end])
    if len(windows)<=4:return windows
    return [windows[round(i*(len(windows)-1)/3)] for i in range(4)]


def original_quotes(db: sqlite3.Connection,subject_id: str,query: str,tier: ModelTier,rows: list[dict[str,object]],provider: CandidateProvider|None,lang: Lang) -> tuple[str, tuple[tuple[str, str], ...]]:
    from .recall import _query_eligible_rows,_anchor_is_explicit,_bigrams
    from .batch_adapter import _source_privacy_support_error, _memory_management_support_error
    scorer=getattr(provider,'score_texts',None)
    if provider is None or not provider.config.enabled or not callable(scorer):return '', ()
    owner,names,other_names,ambiguous=_scope(db,subject_id,query,rows,tier)
    if ambiguous:return '', ()
    eligible={(str(r['kind']),str(r['id'])) for r in _query_eligible_rows(query,rows)}
    if owner:eligible.update((str(r['kind']),str(r['id'])) for r in _profile_rows(db,subject_id,rows))
    candidates=[]
    source_links: dict[str, tuple[tuple[str, str], ...]] = {}
    source_texts: dict[str, str] = {}
    for eid,timestamp,raw in db.execute("SELECT id,occurred_at,raw_content FROM evidence WHERE subject_id=? AND source_kind='spoken' AND deleted_at IS NULL ORDER BY occurred_at DESC,id LIMIT 256",(subject_id,)):
        eid,text=str(eid),str(raw)
        source_support = ((eid, 0, len(text), text),)
        if (_source_privacy_support_error(source_support, {eid: text})
                or _memory_management_support_error(source_support, {eid: text})):
            continue
        if not _all_evidence_current(db,[eid],subject_id=subject_id,surface='model_cloud' if tier=='cloud' else 'recall',model_tier=tier):continue
        links=[(kind,str(i)) for kind,table,col in (('cognition','cognition_evidence','cognition_id'),('relationship','relationship_evidence','relationship_id'),('event','world_event_evidence','world_event_id')) for i, in db.execute(f'SELECT {col} FROM {table} WHERE evidence_id=?',(eid,))]
        if any(key not in eligible for key in links):continue
        if not links:
            if other_names and not any(_anchor_is_explicit(text.casefold(),n.casefold()) for n in other_names):continue
            if not other_names and not re.match(r"^(?:I\b|I['’]|My\b|我|本人)",text.strip(),re.I):continue
            if re.match(r'^I\s+(?:think|believe|know|heard|feel)\s+(?:that\s+)?(?:they|he|she)\b',text.strip(),re.I):continue
        source_links[eid] = tuple(links)
        source_texts[eid] = text
        for window in _windows(text):
            if window.strip():candidates.append((eid,str(timestamp),window,len(text)>len(window)))
    if not candidates:return '', ()
    queries=_facets(query,names)
    try:
        many=getattr(provider,'score_texts_many',None)
        matrix=many(queries,[c[2] for c in candidates]) if callable(many) else [scorer(q,[c[2] for c in candidates]) for q in queries]
        if len(matrix)!=len(queries) or any(len(s)!=len(candidates) for s in matrix):return '', ()
        rankings=[sorted((i for i,v in enumerate(scores) if math.isfinite(v) and provider.config.threshold<=v<=1.00001),key=lambda i:(-scores[i],candidates[i][0],i)) for scores in matrix]
        chosen: list[int]=[]
        per_source: dict[str,int]={}
        lines: list[str]=[]
        for _ in range(QUOTE_ITEMS):
            added=False
            for ranked in rankings:
                for i in ranked:
                    eid,timestamp,text,partial=candidates[i]
                    source_limit = 2 if source_links[eid] else 1
                    if i in chosen or per_source.get(eid,0)>=source_limit:continue
                    # Unformed speech has no accepted claim range proving who
                    # said a cropped sentence. Keep the entire attribution;
                    # sources that cannot fit the existing quote budget wait.
                    if not source_links[eid]:
                        text, partial = source_texts[eid], False
                    terms=_bigrams(text)
                    if any(len(terms&_bigrams(candidates[j][2]))/max(1,len(terms|_bigrams(candidates[j][2])))>0.7 for j in chosen):continue
                    label='User original' if lang=='en' else '用户原话'
                    suffix=(' [excerpt]' if lang=='en' else '［节选］') if partial else ''
                    line=f'{label} [{timestamp}]'+suffix+': '+text
                    if len('\n'.join(lines))+bool(lines)+len(line)>QUOTE_CHARS:continue
                    chosen.append(i);per_source[eid]=per_source.get(eid,0)+1;lines.append(line);added=True
                    break
                if len(chosen)==QUOTE_ITEMS:break
            if len(chosen)==QUOTE_ITEMS or not added:break
        dependencies = tuple(dict.fromkeys(
            reference for i in chosen
            for reference in (('evidence', candidates[i][0]), *source_links[candidates[i][0]])
        ))
        return '\n'.join(lines), dependencies
    except Exception:
        return '', ()
