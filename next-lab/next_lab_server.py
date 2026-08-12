"""Loopback-only stdlib HTTP server for MemoWeft Next Lab."""
from __future__ import annotations

import argparse
import json
import socket
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable

from next_lab_core import LabService, REPO_ROOT


LEGACY_HTML = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MemoWeft Next 实验工作台</title><style>body{visibility:hidden}
:root{color-scheme:light;--paper:#F6F4EF;--panel:#FFFEFB;--ink:#26231F;--muted:#766F66;--line:#D9D3C8;--action:#A4432F;--world:#3F6F73;--prov:#655B87;--warning:#9C5B25;--focus:#1D5E63;font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}*{box-sizing:border-box}html,body{max-width:100%}body{margin:0;background:var(--paper);color:var(--ink)}button,select,textarea{font:inherit;color:inherit}button{border:1px solid #803222;background:var(--action);color:#fff;border-radius:4px;padding:.55rem .75rem;cursor:pointer;font-weight:650}button.secondary{background:var(--panel);color:var(--ink);border-color:var(--line)}button:disabled{opacity:.55;cursor:not-allowed}button:focus-visible,select:focus-visible,textarea:focus-visible,[tabindex]:focus-visible{outline:3px solid var(--focus);outline-offset:2px}header{padding:1.1rem clamp(1rem,3vw,3rem);border-bottom:1px solid var(--line);display:flex;gap:1rem;align-items:center;justify-content:space-between;min-width:0}header>div{min-width:0;overflow-wrap:anywhere}h1{font:700 1.35rem/1.15 Georgia,serif;margin:0}h2{font:700 1rem/1.2 Georgia,serif;margin:.2rem 0 .6rem}.sub,.small{color:var(--muted);font-size:.86rem}.wrap{width:100%;min-width:0;padding:1rem clamp(1rem,3vw,3rem);max-width:1600px;margin:auto}.banner{border-left:4px solid var(--warning);padding:.65rem .8rem;background:#FFF7EA;margin-bottom:1rem;overflow-wrap:anywhere}.steps{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:.45rem;margin-bottom:1rem;min-width:0}.step,.panel{border:1px solid var(--line);background:var(--panel);padding:.55rem;min-width:0;overflow-wrap:anywhere}.step{min-height:76px}.step strong{display:block}.step.locked{color:var(--muted);background:#F0ECE5}.step.reference{border-top:3px solid var(--warning)}.grid{display:grid;grid-template-columns:minmax(0,280px) minmax(0,1fr) minmax(0,360px);gap:1rem;min-width:0}.panel{padding:1rem}.scenario{width:100%;text-align:left;background:transparent;color:var(--ink);border-color:transparent;border-left:4px solid transparent;margin:.15rem 0}.scenario[aria-pressed=true]{border-color:var(--world);background:#EAF1EF}.badge{display:inline-block;font-size:.75rem;padding:.1rem .35rem;border:1px solid var(--line);border-radius:99px;background:var(--panel);overflow-wrap:anywhere}.owner{color:#703E12;border-color:#D9A872;background:#FFF7EA}.status{margin:.5rem 0;padding:.45rem .55rem;border-left:3px solid var(--world);background:#EDF4F2}.graph{height:300px;border:1px solid var(--line);overflow:auto;position:relative;background:#FCFBF7;max-width:100%}.graph svg{width:100%;height:100%;min-width:560px}table{border-collapse:collapse;width:100%;font-size:.84rem;margin-top:.7rem}th,td{border-bottom:1px solid var(--line);padding:.4rem;text-align:left;vertical-align:top;word-break:break-word}th{color:var(--muted)}details{margin:.7rem 0;min-width:0}details>div,#table,#retainedLedger,#reviewHistory,#comparison{max-width:100%;overflow-x:auto}summary{cursor:pointer;font-weight:650}.checks li{margin:.35rem 0}.passed{color:#24654E}.failed{color:#A83B26}.not-run{color:var(--muted)}textarea{width:100%;min-height:75px;border:1px solid var(--line);background:#fff;padding:.4rem}.actions{display:flex;gap:.45rem;flex-wrap:wrap}#notice{min-height:1.5rem;font-weight:650}.sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}pre{white-space:pre-wrap;word-break:break-word}@media(max-width:1100px){.grid{grid-template-columns:minmax(0,245px) minmax(0,1fr)}.right{grid-column:1/-1}.steps{grid-template-columns:repeat(3,1fr)}}@media(max-width:768px){header{align-items:flex-start;flex-direction:column}.grid{grid-template-columns:minmax(0,1fr)}.right{grid-column:auto}.steps{grid-template-columns:minmax(0,1fr)}.wrap{padding:1rem}.graph{height:250px}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important;animation:none!important}}
</style></head><body><header><div><h1>MemoWeft Next Lab</h1><div class="sub">Gate 0 workbench · local semantic inspection, not a product frontend</div></div><div id="serverStatus" class="badge">Loading local status…</div></header><main class="wrap"><div class="banner"><strong>Honesty boundary.</strong> Every scenario is a manual semantic oracle. Nanjing has no frozen raw transcript; Evidence IDs are references only. Test green, semantic checks, live model, full journey, and Owner verdict are separate evidence states.</div><section class="steps" aria-label="Stage 0 steps" id="steps"></section><div class="grid"><aside class="panel"><h2>Manual Golden scenarios</h2><p class="small">Scenario source and execution identity are recorded per retained run.</p><div id="scenarioList" role="group" aria-label="Scenarios"></div><hr><div class="actions"><button id="run">Run selected</button><button class="secondary" id="runAll">Run all five</button></div><p id="notice" role="status" aria-live="polite"></p></aside><section class="panel"><div class="actions" style="justify-content:space-between"><div><h2 id="title">Choose a scenario</h2><div id="scenarioMeta" class="small"></div></div><button class="secondary" id="expand" disabled>Local expansion</button></div><div id="boundary"></div><div class="actions" style="margin:.7rem 0"><button class="secondary" data-view="world" aria-pressed="true">World Graph</button><button class="secondary" data-view="provenance" aria-pressed="false">Provenance Graph</button></div><div class="graph" id="graph" aria-label="Graph visualization" tabindex="0"></div><details open><summary>Accessible graph table</summary><div id="table"></div></details><details><summary>Cognition inspector</summary><div id="inspector" class="small">Run a scenario to inspect each cognition's target, perspective, and provenance references.</div></details><details open><summary>Baseline comparison</summary><div id="comparison" class="small">Pin a complete scenario run, then compare it with a later complete run.</div></details></section><aside class="panel right"><h2>Run & review ledger</h2><div id="runInfo" class="small">No retained run for this scenario.</div><hr><h2>Owner verdict</h2><p class="small">Canonical append-only local ledger. A local note is not Gate 0 acceptance and never mutates a world.</p><label for="verdict">Verdict</label><select id="verdict"><option value="needs-discussion">Needs discussion</option><option value="accept-structure">Accept structure</option><option value="reject-structure">Reject structure</option></select><label class="sr" for="notes">Review notes</label><textarea id="notes" placeholder="Boundary reasoning or follow-up question"></textarea><button id="recordReview" class="secondary">Append local note</button><hr><div class="actions"><button id="pin" class="secondary" disabled>Pin baseline</button><button id="compare" class="secondary" disabled>Compare baseline</button><button id="rerunFailed" class="secondary" disabled>Rerun failures only</button></div><details open><summary>Retained run ledger</summary><div id="retainedLedger"></div></details><details open><summary>Selected scenario review history / notes</summary><div id="reviewHistory"></div></details><details><summary>Environment & evidence states</summary><pre id="environment" class="small"></pre></details></aside></div></main><script>
const $=id=>document.getElementById(id);const esc=value=>String(value).replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const state={scenarios:[],runs:[],reviews:[],selected:null,activeRun:null,view:'world',busy:false,status:null};
const api=(path,body)=>fetch(path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined}).then(async response=>{const payload=await response.json();if(!response.ok)throw Error(payload.error||response.status);return payload});
function announce(message){const developerNotice=$('notice'),ownerNotice=$('ownerNotice');if(developerNotice)developerNotice.textContent=message;if(ownerNotice)ownerNotice.textContent=message}function scenario(){return state.scenarios.find(item=>item.id===state.selected)||null}function scenarioRun(run){return run&&run.scenarios.find(item=>item.scenarioId===state.selected)||null}function isComplete(run){const item=scenarioRun(run);return Boolean(run&&item&&!run.partial&&item.graph&&!item.checks.some(check=>check.state==='not-run'))}function sameScenarioScope(left,right){const a=scenarioRun(left),b=scenarioRun(right);return isComplete(left)&&isComplete(right)&&a.definitionHash===b.definitionHash&&a.checks.map(check=>check.id).join('|')===b.checks.map(check=>check.id).join('|')}function latestRun(){return [...state.runs].reverse().find(run=>scenarioRun(run))||null}function baselineRun(){const item=scenario();return item&&state.runs.find(run=>run.id===item.baselineRunId)||null}
function syncActiveRun(){state.activeRun=latestRun()}function renderSteps(){const status=state.status;if(!status)return;$('steps').innerHTML=status.pipeline.map(item=>`<div class="step ${item.state==='locked'?'locked':item.state==='reference-only'?'reference':''}"><strong>${esc(item.name)}</strong><span class="badge">${esc(item.state)}</span><div class="small">${esc(item.detail)}</div></div>`).join('');$('serverStatus').textContent=`${esc(status.stage)} · Model: ${esc(status.model.stage0)}`;$('environment').textContent=JSON.stringify({branch:status.reproducibility.branch,head:status.reproducibility.head,dirtyFingerprint:status.reproducibility.dirtyFingerprint,stage:status.reproducibility.stageGate,fixture:status.reproducibility.fixture,model:status.model,gateReviewDocument:status.gateReviewDocument,stateWarnings:status.stateWarnings},null,2)}
function renderScenarios(){$('scenarioList').innerHTML=state.scenarios.map(item=>`<button class="scenario" aria-pressed="${item.id===state.selected}" data-id="${esc(item.id)}" ${state.busy?'disabled':''}><strong>${esc(item.title)}</strong><br><span class="small">${esc(item.summary)}</span><br><span class="badge">manual oracle</span> ${item.semanticStatus==='owner-review-required'?'<span class="badge owner">Owner boundary</span>':''}</button>`).join('');document.querySelectorAll('.scenario').forEach(button=>button.onclick=()=>{if(state.busy)return;state.selected=button.dataset.id;syncActiveRun();renderAll()})}
function renderGraph(item){const graph=item.views[state.view],color=state.view==='world'?'#3F6F73':'#655B87',nodes=graph.nodes.slice(0,16),points=nodes.map((node,index)=>({node,x:70+(index%4)*155,y:45+Math.floor(index/4)*75})),positions=new Map(points.map(point=>[point.node.id,point]));$('graph').innerHTML=`<svg viewBox="0 0 700 330" role="img" aria-label="${esc(state.view)} graph with ${nodes.length} visible nodes">${graph.edges.filter(edge=>positions.has(edge.from)&&positions.has(edge.to)).map(edge=>`<line x1="${positions.get(edge.from).x}" y1="${positions.get(edge.from).y}" x2="${positions.get(edge.to).x}" y2="${positions.get(edge.to).y}" stroke="#B8B0A3"/><text x="${(positions.get(edge.from).x+positions.get(edge.to).x)/2}" y="${(positions.get(edge.from).y+positions.get(edge.to).y)/2}" font-size="9" fill="#766F66">${esc(edge.kind)}</text>`).join('')}${points.map(point=>`<circle cx="${point.x}" cy="${point.y}" r="18" fill="#FFFEFB" stroke="${color}" stroke-width="2"/><text x="${point.x}" y="${point.y+32}" text-anchor="middle" font-size="10" fill="#26231F">${esc(point.node.label).slice(0,22)}</text>`).join('')}</svg>`;$('table').innerHTML=`<table><thead><tr><th>ID</th><th>Kind</th><th>Label</th></tr></thead><tbody>${graph.nodes.map(node=>`<tr><td>${esc(node.id)}</td><td>${esc(node.kind)}</td><td>${esc(node.label)}</td></tr>`).join('')}</tbody></table>`}
function renderInspector(item){let html=item.graph.cognitions.length?`<table><thead><tr><th>Cognition</th><th>Target</th><th>Perspective</th><th>Provenance</th></tr></thead><tbody>${item.graph.cognitions.map(cognition=>`<tr><td>${esc(cognition.content)}</td><td>${esc(cognition.target.kind)} · ${esc(cognition.target.id)}</td><td>${esc(cognition.perspective.kind)} · ${esc((cognition.perspective.holder_entity_ids||[]).join(', ')||'none')}</td><td>${esc(cognition.sources.map(source=>source.evidence_id+' ('+source.relation+')').join(', ')||'No Evidence reference')}</td></tr>`).join('')}</tbody></table>`:'No cognitions in this manual oracle.';const artifacts=item.graph.provenanceArtifacts;if(artifacts){html+=`<h3>Golden #3 typed provenance artifacts</h3><p><strong>Spoken Evidence</strong> · ${esc(artifacts.evidence.map(evidence=>evidence.id+': '+evidence.raw_content).join(' | '))}</p><p><strong>InteractionContext — non-evidence</strong> · ${esc(artifacts.interactionContext.context.map(turn=>turn.role+': '+turn.content).join(' | '))}</p><pre class="small">${esc(JSON.stringify(artifacts.interactionContext,null,2))}</pre><p><strong>SemanticResolution</strong> · ${esc(artifacts.semanticResolution.id)} · evidence ${esc(artifacts.semanticResolution.evidence_id)} · origin ${esc(artifacts.semanticResolution.proposition_origin)}</p>`}$('inspector').innerHTML=html}
function renderSelected(){const selected=scenario(),run=state.activeRun,item=scenarioRun(run);$('title').textContent=selected?selected.title:'Choose a scenario';$('scenarioMeta').textContent=selected?`${selected.checkCount} allowlisted Golden checks · ${selected.semanticStatus} · baseline ${selected.baselineRunId||'not pinned'}`:'';$('boundary').innerHTML=selected?`<div class="status ${selected.semanticStatus==='owner-review-required'?'owner':''}"><strong>Boundary question</strong><br>${esc(selected.boundaryQuestion)}</div>`:'';document.querySelectorAll('[data-view]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.view===state.view)));if(item&&item.graph){renderGraph(item);renderInspector(item)}else{$('graph').textContent='Run this manual oracle to render its real graph.';$('table').textContent='';$('inspector').textContent='Run a scenario to inspect its cognitions.'}}
function renderLedger(){const current=state.activeRun,currentItem=scenarioRun(current),baseline=baselineRun(),selectedReviews=state.reviews.filter(review=>review.scenarioId===state.selected);if(currentItem){$('runInfo').innerHTML=`<strong>Active: ${esc(current.id)}</strong><br>${esc(current.createdAt)} · ${current.partial?'partial':'complete'}<br>identity ${esc(current.identity||'legacy / not recorded')}<br>world ${esc(currentItem.worldHash)}<br>provenance artifacts ${esc(currentItem.provenanceArtifactsHash||'none')}<br>stale: ${current.stale?esc(current.staleReasons.join(', ')):'no'}<ul class="checks">${currentItem.checks.map(check=>`<li class="${esc(check.state)}">${esc(check.id)} — ${esc(check.state)}${check.error?': '+esc(check.error):''}</li>`).join('')}</ul>`}else{$('runInfo').textContent='No retained run for this scenario.'}$('retainedLedger').innerHTML=`<table><thead><tr><th>Run</th><th>Scope</th><th>Baseline</th><th>Stale reasons</th><th>Identity</th></tr></thead><tbody>${state.runs.filter(run=>scenarioRun(run)).map(run=>`<tr><td>${esc(run.id)}<br>${esc(run.createdAt)}</td><td>${run.partial?'partial':'complete'}</td><td>${run.baselineFor===state.selected?'pinned':'—'}</td><td>${run.stale?esc(run.staleReasons.join(', ')):'—'}</td><td>${esc(run.identity||'legacy / not recorded')}</td></tr>`).join('')||'<tr><td colspan="5">No retained scenario runs.</td></tr>'}</tbody></table>`;$('reviewHistory').innerHTML=selectedReviews.length?`<table><thead><tr><th>When</th><th>Verdict</th><th>Run / world</th><th>Local note</th></tr></thead><tbody>${selectedReviews.map(review=>`<tr><td>${esc(review.recordedAt)}</td><td>${esc(review.verdict)}</td><td>${esc(review.runId)}<br>${esc(review.worldHash)}</td><td>${esc(review.notes||'—')}</td></tr>`).join('')}</tbody></table>`:'No local review note for this scenario.';const currentComplete=isComplete(current),baselineComplete=isComplete(baseline);if(current&&!currentComplete)$('comparison').textContent='Partial or incomplete scenario scope cannot be pinned or compared; run the full selected scenario.';else if(baseline&&!baselineComplete)$('comparison').textContent='The retained baseline is partial or incompatible; pin a complete selected-scenario run.';$('expand').disabled=state.busy||!currentItem||!currentItem.graph;$('recordReview').disabled=state.busy||!currentItem||!currentItem.graph;$('pin').disabled=state.busy||!currentComplete;$('rerunFailed').disabled=state.busy||!currentItem;$('compare').disabled=state.busy||!currentComplete||!baselineComplete}
function renderAll(){renderSteps();renderScenarios();renderSelected();renderLedger();$('run').disabled=state.busy||!state.selected;$('runAll').disabled=state.busy||!state.scenarios.length}
async function refresh(){const [status,scenarioResponse,runsResponse,reviewsResponse]=await Promise.all([api('/api/status'),api('/api/scenarios'),api('/api/runs'),api('/api/reviews')]);state.status=status;state.scenarios=scenarioResponse.scenarios;state.runs=runsResponse.runs;state.reviews=reviewsResponse.reviews;if(!state.selected||!state.scenarios.some(item=>item.id===state.selected))state.selected=state.scenarios[0]?.id||null;syncActiveRun()}
async function withBusy(work){if(state.busy)return;state.busy=true;renderAll();try{return await work()}finally{state.busy=false;renderAll()}}
async function run(ids){await withBusy(async()=>{announce('Running allowlisted Golden checks…');await api('/api/run',{scenarioIds:ids});await refresh();announce('Run recorded. This is deterministic contract evidence, not Owner acceptance.')})}
$('run').onclick=()=>{if(state.selected)run([state.selected]).catch(error=>announce('Run failed: '+error.message))};$('runAll').onclick=()=>run(state.scenarios.map(item=>item.id)).catch(error=>announce('Run failed: '+error.message));document.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>{state.view=button.dataset.view;renderSelected()});$('expand').onclick=()=>withBusy(async()=>{const item=scenarioRun(state.activeRun),event=item&&item.graph.events[0];if(!event)throw Error('No current scenario graph is available for local expansion.');const result=await api('/api/expand',{scenarioId:state.selected,target:{kind:'event',id:event.id},depth:1});announce(`${result.label}: ${result.slice.entity_ids.length} entities, ${result.slice.relationship_ids.length} relationships, ${result.slice.event_ids.length} events.`)}).catch(error=>announce('Expansion unavailable: '+error.message));$('recordReview').onclick=()=>withBusy(async()=>{const item=scenarioRun(state.activeRun);const result=await api('/api/review',{runId:state.activeRun.id,scenarioId:state.selected,worldHash:item.worldHash,verdict:$('verdict').value,notes:$('notes').value});$('notes').value='';await refresh();announce(`Local note appended: ${result.id}. World unchanged; this is not Gate 0 acceptance.`)}).catch(error=>announce('Review not recorded: '+error.message));$('pin').onclick=()=>withBusy(async()=>{const result=await api('/api/pin',{runId:state.activeRun.id,scenarioId:state.selected});await refresh();announce(`Pinned ${result.runId} as the ${state.selected} baseline.`)}).catch(error=>announce('Baseline not pinned: '+error.message));$('rerunFailed').onclick=()=>withBusy(async()=>{const result=await api('/api/rerun',{runId:state.activeRun.id,onlyFailed:true});if(result.kind){announce(result.message)}else{await refresh();announce('Failed checks rerun as a new retained record.')}}).catch(error=>announce('Rerun unavailable: '+error.message));$('compare').onclick=()=>withBusy(async()=>{const baseline=baselineRun(),current=state.activeRun;let right=current;if(current&&baseline&&current.id===baseline.id){right=[...state.runs].reverse().find(run=>run.id!==baseline.id&&sameScenarioScope(run,baseline))||null}if(!baseline||!right||!sameScenarioScope(baseline,right)){announce('No second complete retained run with the same selected-scenario scope exists; run this scenario again before comparing the pinned baseline.');return}const result=await api('/api/compare',{leftRunId:baseline.id,rightRunId:right.id,scenarioId:state.selected});$('comparison').innerHTML=`<p><strong>Baseline ${esc(result.leftRunId)}</strong> → <strong>current ${esc(result.rightRunId)}</strong></p><p class="small">${esc(result.leftWorldHash)} → ${esc(result.rightWorldHash)}</p>${result.changes.length?`<table><thead><tr><th>Path</th><th>Before</th><th>After</th></tr></thead><tbody>${result.changes.map(change=>`<tr><td>${esc(change.path)}</td><td><pre>${esc(JSON.stringify(change.before))}</pre></td><td><pre>${esc(JSON.stringify(change.after))}</pre></td></tr>`).join('')}</tbody></table>`:'<p>No structural changes in the selected scenario graph.</p>'}`;announce(`Baseline comparison rendered with ${result.changes.length} structural change(s).`)}).catch(error=>announce('Comparison unavailable: '+error.message));
const ZH_SCENARIOS={
  'golden-1-nanjing':{title:'Golden #1 · 南京冲突',summary:'事件、关系、独立的 Friend_X 认知，以及目标、视角和溯源。',boundary:'这个局部结构是否像一段真实经历，而不是一段 User Profile 描述？'},
  'golden-2-mother-candy':{title:'Golden #2 · 母亲送糖',summary:'重复的关照属于关系模式；它不是对内心偏好的臆测。',boundary:'不推断母亲私人偏好的前提下，这个关系模式是否有用？'},
  'golden-3-ai-shared-experience':{title:'Golden #3 · AI 共同经历',summary:'Agent 参与事件；确认后的认知默认属于用户视角，且只有用户确认才是 Evidence。',boundary:'用户确认后的认知是否与 Agent 提议和事件参与清晰分离？'},
  'golden-4-relationship-repair':{title:'Golden #4 · 冲突、道歉与修复',summary:'三段有 Evidence 引用的历史保持可查询；修复属于关系目标认知，不覆盖关系身份。',boundary:'修复是否保持为有 Evidence 支持的关系目标认知或派生投影，同时保留三段历史？'},
  'golden-5-dormant-friend':{title:'Golden #5 · 长期沉寂的朋友',summary:'只保留身份、关系和重要历史；显著度留给后续按查询与时间派生。',boundary:'世界模型是否不保存长期 status 或 active_salience，而仍保留身份、关系和历史？'}
};
const ZH_PIPELINE={
  'Evidence ref':['Evidence 引用','仅引用','没有冻结的南京原始转录；ID 仅为引用。'],
  'Manual oracle':['手工语义 Oracle','可用','五个手工构建的语义 Golden 场景。'],
  'WorldDelta':['WorldDelta','锁定','Gate 0 Owner 复核完成后的 Stage 1 才可启用 WorldDelta。'],
  'Apply':['Apply','锁定','Apply 已锁定；不存在规范世界的变更路径。'],
  'Recall':['Recall','锁定','Recall 已锁定；局部展开不等于自然语言 Recall。'],
  'Answer':['Answer','锁定','Answer 已锁定；Stage 0 不作召回—回答能力声明。'],
  'Correction':['Correction','锁定','Correction 已锁定；本地 Owner 备注不会解锁纠正或重新 Apply。']
};
const ZH_STATUS={available:'可用',locked:'锁定','reference-only':'仅引用',passed:'通过',failed:'失败','not-run':'未运行',complete:'完整',partial:'部分执行'};
const ZH_VERDICTS={'needs-discussion':'需要讨论','accept-structure':'接受结构','reject-structure':'拒绝结构'};
const ZH_STALE_REASONS={branch:'分支已变化',head:'源码版本已变化',dirtyFingerprint:'工作树内容已变化',stageGate:'Stage / Gate 已变化','fixture-manifest':'fixture 清单已变化','fixture-payload':'fixture 内容已变化','scenario-definition':'场景定义已变化','scenario-source':'场景源码已变化'};
const ZH_KINDS={world:'世界（world）',entity:'实体（entity）',relationship:'关系（relationship）',event:'事件（event）',cognition:'认知（cognition）','spoken-evidence':'口述 Evidence','evidence-reference':'Evidence 引用','interaction-context':'InteractionContext','semantic-resolution':'SemanticResolution'};
const zh=value=>ZH_STATUS[value]||value;
function zhStale(reason){const separator=reason.indexOf(':'),key=separator<0?reason:reason.slice(0,separator),detail=separator<0?'':reason.slice(separator+1),label=ZH_STALE_REASONS[key];return label?`${label}${detail?`（${detail}）`:''}`:reason;}
const OWNER_STORIES={
  'golden-1-nanjing':{label:'南京旅行吵架',story:'你和朋友计划去南京，因为喜欢的旅行方式不同而发生争执。',remember:['人物：你和这位朋友','关系：你们是朋友','经历：计划南京旅行时，因为要不要提前安排详细行程而争执','你的旅行偏好：开车时不固定路线，重视未知和临场探索','你眼中的朋友：喜欢提前做行程，并按攻略游玩','关系判断（候选）：计划性与自由度的差异，可能成为共同出行的摩擦点'],notRemember:['不会把这次争执缩成“你是一个爱吵架的人”','不会把你对朋友的看法伪装成已经证实的客观事实','不会凭空补出没有说过的旅行细节'],decision:'这段南京经历、双方偏好与关系摩擦点，是否应该这样分开保留？'},
  'golden-2-mother-candy':{label:'妈妈多次送糖',story:'妈妈在两次不同的时间给你糖。',remember:['人物：你和妈妈','关系：你是妈妈的孩子','两段经历：妈妈曾两次给你糖','关系判断（推断）：妈妈有重复给你糖的模式'],notRemember:['不会擅自推断“妈妈喜欢糖”','不会把送糖模式写成妈妈永远不变的性格或偏好'],decision:'两次送糖是否应该在你和妈妈的关系上形成“重复送糖”的模式，而不是写成妈妈的个人属性？'},
  'golden-3-ai-shared-experience':{label:'你确认了 AI 的解释',story:'AI 提出“计划方式不同造成了摩擦”，你明确确认它说得对。',remember:['参与者：你和 MemoWeft AI','对话经历：AI 先提出解释，你随后确认','确认后的判断：计划方式不同确实是摩擦的一部分（默认用户视角）','事实依据：只有你说出的确认可以支持这条判断','对话背景：AI 的原提议只保留为上下文'],notRemember:['不会把 AI 自己的话当成事实依据','不会让 AI 为自己的说法作证'],decision:'确认后的认知默认属于你的视角；“确认后的判断只属于你的看法，还是属于你和 AI 共同持有的看法？”这一问的默认答案是前者，只有明确建立共同持有时才使用联合视角。'},
  'golden-4-relationship-repair':{label:'冲突后和好',story:'你和朋友发生冲突，朋友随后道歉，你们后来修复了关系。',remember:['人物与关系：你、朋友和你们的友谊','经历一：你们发生过冲突','经历二：朋友向你道歉','经历三：你们后来修复了友谊','关系认知：有 Evidence 支持的“关系已修复”判断'],notRemember:['不会因为后来和好就抹掉冲突和道歉','不会把修复写成关系身份上的永久 status'],decision:'修复保留为关系目标认知或按查询派生的当前投影，三段历史永不覆盖；“已修复”应该直接写成关系的当前状态这一候选已被否定。'},
  'golden-5-dormant-friend':{label:'很久没联系的老朋友',story:'一位老朋友很久没有联系，但你们曾一起庆祝毕业。',remember:['人物与关系：你、这位老朋友和你们的友谊','重要经历：2018 年你们一起庆祝毕业','后续查询可按时间与语境派生显著度'],notRemember:['不会因为很久没联系就删除这位朋友或友谊','不会把“现在不常想起”误写成“关系不存在”','不会因为当前活跃度低就丢掉共同毕业的经历','不会把一个低活跃快照说成已经实现了自动衰减或召回排序'],decision:'关系对象只保留身份与历史；显著度不写入长期字段，留给后续按查询和时间动态计算。“很久没联系／现在不常想起”应该写进关系本身这一候选已被否定。'}
};
function buildOwnerFirstShell(){
  if($('ownerFlow'))return;const main=document.querySelector('main.wrap'),banner=document.querySelector('.banner'),steps=$('steps'),grid=document.querySelector('.grid'),right=grid.querySelector('.right'),developer=document.createElement('details'),flow=document.createElement('section');
  document.head.insertAdjacentHTML('beforeend','<style>.owner-flow{display:grid;gap:1rem;max-width:900px;margin:0 auto}.owner-card{border:1px solid var(--line);background:var(--panel);padding:clamp(1rem,3vw,1.5rem);border-radius:6px}.story-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:.55rem}.story-choice{background:#FFFEFB;color:var(--ink);border-color:var(--line);text-align:left;min-height:90px}.story-choice[aria-pressed=true]{border-color:var(--world);background:#EAF1EF}.memory-plan{border-left:4px solid var(--world);padding:.75rem 1rem;background:#EDF4F2}.result-count{font:700 1.35rem/1 Georgia,serif}.owner-verdicts{display:flex;gap:.5rem;flex-wrap:wrap;margin:.6rem 0}.owner-verdicts button[aria-pressed=true]{background:var(--action);color:#fff;border-color:#803222}.developer-details{margin-top:1.25rem}.developer-details>summary{padding:.7rem 0;color:var(--muted)}.developer-details .steps{margin-top:1rem}@media(max-width:768px){.owner-card{padding:1rem}}</style>');
  banner.innerHTML='<strong>这页只做一件事：</strong> 看一段故事，再判断 MemoWeft 打算采用的记法是否符合你的直觉。';document.querySelector('header .sub').textContent='用五个故事确认 MemoWeft 的记法是否合理';$('serverStatus').textContent='本地工作台';
  flow.id='ownerFlow';flow.className='owner-flow';flow.innerHTML='<section class="owner-card"><h2>① 选择一个故事</h2><p class="small">先选一段你最容易判断的经历。</p><div id="ownerScenarioChoices" class="story-list" role="group" aria-label="故事选择"></div></section><section class="owner-card"><h2>② MemoWeft 会记住这些</h2><p id="ownerStory" class="small"></p><p class="small">这是当前设计中的记法，不代表 MemoWeft 已经能从对话自动生成或自然语言回忆这些内容。</p><div class="story-list"><section class="memory-plan"><strong>会记住这些</strong><ul id="ownerRemember"></ul></section><section class="memory-plan"><strong>明确不会乱记</strong><ul id="ownerNotRemember"></ul></section><section class="memory-plan"><strong>待你决定</strong><p id="ownerDecision"></p></section></div></section><section class="owner-card"><h2>③ 你的判断</h2><p class="small">你的意见只保存在本机，帮助我们决定这种记法要不要继续；它不会改变任何记忆，也不会让 Gate 0 自动通过。</p><div id="ownerVerdictButtons" class="owner-verdicts"><button type="button" data-verdict="accept-structure" aria-pressed="false">符合直觉</button><button type="button" data-verdict="needs-discussion" aria-pressed="true">不确定</button><button type="button" data-verdict="reject-structure" aria-pressed="false">不符合</button></div><div id="ownerReviewControls"></div><p id="ownerNotice" role="status" aria-live="polite"></p></section>';
  const reviewControls=flow.querySelector('#ownerReviewControls');['label[for="notes"]','#notes','#recordReview'].forEach(selector=>{const node=right.querySelector(selector);if(node)reviewControls.append(node)});const notesLabel=reviewControls.querySelector('label[for="notes"]'),notes=reviewControls.querySelector('#notes'),recordReview=reviewControls.querySelector('#recordReview');if(notesLabel){notesLabel.className='';notesLabel.textContent='补充说明（可选）';}notes.placeholder='可以写下哪里不对，或你希望它怎么记';recordReview.textContent='保存我的意见';
  developer.id='developerDetails';developer.className='developer-details';developer.open=false;developer.innerHTML='<summary>开发者详情（默认关闭）</summary>';
  steps.hidden=false;developer.append(steps);developer.append(grid);main.append(flow);main.append(developer);
}
function renderOwnerJourney(){
  const choices=$('ownerScenarioChoices');if(!choices)return;const selected=scenario(),story=selected&&OWNER_STORIES[selected.id];choices.innerHTML=state.scenarios.map(item=>{const copy=OWNER_STORIES[item.id];return `<button class="story-choice" data-story-id="${esc(item.id)}" aria-pressed="${item.id===state.selected}"><strong>${esc(copy.label)}</strong><br><span class="small">${esc(copy.story)}</span></button>`}).join('');choices.querySelectorAll('button').forEach(button=>button.onclick=()=>{if(state.busy)return;state.selected=button.dataset.storyId;syncActiveRun();renderAll()});$('ownerStory').innerHTML=story?`<strong>事情：</strong>${esc(story.story)}`:'请选择一个故事。';$('ownerRemember').innerHTML=story?story.remember.map(item=>`<li>${esc(item)}</li>`).join(''):'<li>选择故事后显示</li>';$('ownerNotRemember').innerHTML=story?story.notRemember.map(item=>`<li>${esc(item)}</li>`).join(''):'<li>选择故事后显示</li>';$('ownerDecision').textContent=story?story.decision:'选择故事后显示。';
  const verdict=$('verdict');$('serverStatus').textContent='本地工作台';$('recordReview').disabled=state.busy||!selected;$('ownerVerdictButtons').querySelectorAll('button').forEach(button=>{button.disabled=state.busy||!selected;button.setAttribute('aria-pressed',String(button.dataset.verdict===verdict.value));button.onclick=()=>{verdict.value=button.dataset.verdict;renderOwnerJourney()}});
}
function diagnostic(context,error){console.error(`MemoWeft Next Lab ${context} diagnostic`,error);}
function safeUiError(context,error){diagnostic(context,error);announce(`${context}未完成。请检查本地状态；诊断信息已保留给维护者。`);}
function localizeStatic(){
  document.documentElement.lang='zh-CN';document.title='MemoWeft Next 实验工作台';
  document.querySelector('header h1').textContent='MemoWeft Next 实验工作台';document.querySelector('header .sub').textContent='Gate 0 工作台 · 本地语义核查，不是产品前端';$('serverStatus').textContent='正在读取本地状态…';
  document.querySelector('.banner').innerHTML='<strong>证据边界。</strong> 每个场景都是手工语义 Oracle。南京场景尚无冻结的原始转录；Evidence ID 仅为引用。测试通过、语义检查、实时模型、完整旅程和 Owner 裁决是彼此独立的证据状态。';
  $('steps').setAttribute('aria-label','Stage 0 步骤');const panels=document.querySelectorAll('.panel');panels[0].querySelector('h2').textContent='手工 Golden 场景';panels[0].querySelector('p.small').textContent='每个保留运行都会记录场景来源与执行身份。';$('scenarioList').setAttribute('aria-label','场景');$('run').textContent='运行所选场景';$('runAll').textContent='运行全部五个场景';
  $('title').textContent='请选择场景';$('expand').textContent='局部展开';document.querySelector('[data-view="world"]').textContent='世界图';document.querySelector('[data-view="provenance"]').textContent='溯源图';$('graph').setAttribute('aria-label','图形可视化');
  const middle=panels[1];const middleSummaries=middle.querySelectorAll('details summary');middleSummaries[0].textContent='可访问的图表';middleSummaries[1].textContent='认知检查器';middleSummaries[2].textContent='基线对比';$('inspector').textContent='运行场景后，可核查每条认知的目标、视角和溯源引用。';$('comparison').textContent='固定一次完整场景运行后，可与后续完整运行比较。';
  const right=panels[2],rightHeadings=right.querySelectorAll('h2'),rightSummaries=right.querySelectorAll('details summary');rightHeadings[0].textContent='运行与复核账本';rightHeadings[1].textContent='Owner 裁决';$('runInfo').textContent='此场景尚无保留运行。';right.querySelector('p.small').textContent='规范的仅追加本地账本。本地备注不是 Gate 0 接受，且绝不改变世界。';right.querySelector('label[for="verdict"]').textContent='裁决';$('verdict').options[0].text='需要讨论';$('verdict').options[1].text='接受结构';$('verdict').options[2].text='拒绝结构';right.querySelector('label[for="notes"]').textContent='复核备注';$('notes').placeholder='边界理由或后续问题';$('recordReview').textContent='追加本地备注';$('pin').textContent='固定基线';$('compare').textContent='比较基线';$('rerunFailed').textContent='仅重跑失败项';rightSummaries[0].textContent='保留运行账本';rightSummaries[1].textContent='所选场景的复核历史 / 备注';rightSummaries[2].textContent='环境与证据状态';document.body.style.visibility='visible';
}
function localizeRendered(){
  const status=state.status;if(status){const stepNodes=document.querySelectorAll('.step');status.pipeline.forEach((item,index)=>{const translated=ZH_PIPELINE[item.name]||[item.name,zh(item.state),item.detail],node=stepNodes[index];if(node){node.querySelector('strong').textContent=translated[0];node.querySelector('.badge').textContent=translated[1];node.querySelector('.small').textContent=translated[2];}});$('serverStatus').textContent=`Stage 0 · 语义宪章 · 模型：${status.model.stage0==='Not required'?'本阶段不要求':status.model.stage0}`;}
  state.scenarios.forEach(item=>{const node=document.querySelector(`.scenario[data-id="${item.id}"]`),copy=ZH_SCENARIOS[item.id];if(node&&copy){node.querySelector('strong').textContent=copy.title;node.querySelector('.small').textContent=copy.summary;node.querySelector('.badge').textContent='手工 Oracle';const owner=node.querySelector('.owner');if(owner)owner.textContent='Owner 边界';}});
  const selected=scenario(),copy=selected&&ZH_SCENARIOS[selected.id];if(copy){$('title').textContent=copy.title;$('scenarioMeta').textContent=`${selected.checkCount} 个允许的 Golden 检查 · ${selected.semanticStatus==='owner-review-required'?'需要 Owner 复核':'等待 Owner 的手工 Oracle'} · 基线 ${selected.baselineRunId||'未固定'}`;$('boundary').innerHTML=`<div class="status ${selected.semanticStatus==='owner-review-required'?'owner':''}"><strong>边界问题</strong><br>${esc(copy.boundary)}</div>`;}
  const svg=$('graph').querySelector('svg');if(svg)svg.setAttribute('aria-label',`${state.view==='world'?'世界图':'溯源图'}，显示 ${svg.querySelectorAll('circle').length} 个可见节点`);const graphHeaders=$('table').querySelectorAll('th');['ID','类型','标签'].forEach((label,index)=>{if(graphHeaders[index])graphHeaders[index].textContent=label});$('table').querySelectorAll('tbody tr').forEach(row=>{const kindCell=row.children[1];if(kindCell&&ZH_KINDS[kindCell.textContent])kindCell.textContent=ZH_KINDS[kindCell.textContent];});
  const inspectorHeaders=$('inspector').querySelectorAll('th');['认知','目标','视角','溯源'].forEach((label,index)=>{if(inspectorHeaders[index])inspectorHeaders[index].textContent=label});const inspectorHeading=$('inspector').querySelector('h3');if(inspectorHeading)inspectorHeading.textContent='Golden #3 类型化溯源构件';if(!$('inspector').querySelector('table')&&!$('inspector').querySelector('h3')&&state.activeRun)$('inspector').textContent='此手工 Oracle 中没有认知。';
  const visibleRuns=state.runs.filter(run=>scenarioRun(run)),ledgerHeaders=$('retainedLedger').querySelectorAll('th');['运行','范围','基线','过期原因','身份'].forEach((label,index)=>{if(ledgerHeaders[index])ledgerHeaders[index].textContent=label});$('retainedLedger').querySelectorAll('tbody tr').forEach((row,index)=>{const run=visibleRuns[index],cells=row.children;if(!run||cells.length<5)return;cells[1].textContent=run.partial?'部分执行':'完整';cells[2].textContent=run.baselineFor===state.selected?'已固定':'—';cells[3].textContent=run.stale?run.staleReasons.map(zhStale).join('；'):'—';cells[4].textContent=run.identity||'历史记录未记录身份';});const selectedReviews=state.reviews.filter(review=>review.scenarioId===state.selected),historyHeaders=$('reviewHistory').querySelectorAll('th');['时间','裁决','运行 / 世界','本地备注'].forEach((label,index)=>{if(historyHeaders[index])historyHeaders[index].textContent=label});$('reviewHistory').querySelectorAll('tbody tr').forEach((row,index)=>{const review=selectedReviews[index],verdictCell=row.children[1];if(review&&verdictCell)verdictCell.textContent=ZH_VERDICTS[review.verdict]||review.verdict;});
  const runInfo=$('runInfo'),currentItem=scenarioRun(state.activeRun);if(currentItem&&state.activeRun){runInfo.innerHTML=`<strong>当前：${esc(state.activeRun.id)}</strong><br>${esc(state.activeRun.createdAt)} · ${state.activeRun.partial?'部分执行':'完整'}<br>运行身份 ${esc(state.activeRun.identity||'历史记录未记录身份')}<br>世界哈希 ${esc(currentItem.worldHash)}<br>溯源构件哈希 ${esc(currentItem.provenanceArtifactsHash||'无')}<br>过期：${state.activeRun.stale?esc(state.activeRun.staleReasons.map(zhStale).join('；')):'否'}<ul class="checks">${currentItem.checks.map(check=>{if(check.error)diagnostic(`检查 ${check.id}`,check.error);return `<li class="${esc(check.state)}">${esc(check.id)} — ${zh(check.state)}${check.error?'；检查未通过（诊断已记录）':''}</li>`}).join('')}</ul>`;}else if(selected){runInfo.textContent='此场景尚无保留运行。';}
  if(!$('retainedLedger').querySelector('table'))$('retainedLedger').textContent='尚无保留的场景运行。';if(!$('reviewHistory').querySelector('table'))$('reviewHistory').textContent='此场景尚无本地复核备注。';
  const comparison=$('comparison');if(comparison.textContent.startsWith('Partial or incomplete'))comparison.textContent='部分或不完整的场景范围不能固定或比较；请先完整运行所选场景。';else if(comparison.textContent.startsWith('The retained baseline'))comparison.textContent='保留的基线不完整或不兼容；请固定一次完整的所选场景运行。';
}
const localizeRenderedOriginal=localizeRendered;localizeRendered=function(){localizeRenderedOriginal();renderOwnerJourney();if(state.status){$('serverStatus').textContent=`${state.status.stage} · 模型：${state.status.model.stage0==='Not required'?'本地模型未启用':state.status.model.stage0}`;}};const renderAllOriginal=renderAll;renderAll=function(){renderAllOriginal();localizeRendered();};const renderSelectedOriginal=renderSelected;renderSelected=function(){renderSelectedOriginal();localizeRendered();};
async function runChinese(ids){await withBusy(async()=>{announce('正在生成记忆示意…');await api('/api/run',{scenarioIds:ids});await refresh();announce('记忆示意已生成，请查看第③步的结果。')})}
$('run').onclick=()=>{if(state.selected)runChinese([state.selected]).catch(error=>safeUiError('运行',error))};$('runAll').onclick=()=>runChinese(state.scenarios.map(item=>item.id)).catch(error=>safeUiError('运行',error));$('expand').onclick=()=>withBusy(async()=>{const item=scenarioRun(state.activeRun),event=item&&item.graph.events[0];if(!event)throw Error('missing-current-scenario-graph');const result=await api('/api/expand',{scenarioId:state.selected,target:{kind:'event',id:event.id},depth:1});announce(`局部展开完成：${result.slice.entity_ids.length} 个实体、${result.slice.relationship_ids.length} 个关系、${result.slice.event_ids.length} 个事件。`)}).catch(error=>safeUiError('局部展开',error));$('recordReview').onclick=()=>withBusy(async()=>{let activeRun=state.activeRun,item=scenarioRun(activeRun);if(!item||!item.worldHash||!item.graph){await api('/api/run',{scenarioIds:[state.selected]});await refresh();syncActiveRun();activeRun=state.activeRun;item=scenarioRun(activeRun)}if(!activeRun||!item||!item.worldHash||!item.graph)throw Error('review-run-unavailable');await api('/api/review',{runId:activeRun.id,scenarioId:state.selected,worldHash:item.worldHash,verdict:$('verdict').value,notes:$('notes').value});$('notes').value='';await refresh();announce('你的意见已保存在本机。它不会改变记忆，也不代表 Gate 已通过。')}).catch(error=>safeUiError('保存意见',error));$('pin').onclick=()=>withBusy(async()=>{const result=await api('/api/pin',{runId:state.activeRun.id,scenarioId:state.selected});await refresh();announce(`已将 ${result.runId} 固定为 ${state.selected} 的基线。`)}).catch(error=>safeUiError('固定基线',error));$('rerunFailed').onclick=()=>withBusy(async()=>{const result=await api('/api/rerun',{runId:state.activeRun.id,onlyFailed:true});if(result.kind)announce('没有可重跑的失败 Golden 检查。');else{await refresh();announce('失败检查已作为新的保留记录重跑。')}}).catch(error=>safeUiError('重跑失败项',error));$('compare').onclick=()=>withBusy(async()=>{const baseline=baselineRun(),current=state.activeRun;let right=current;if(current&&baseline&&current.id===baseline.id)right=[...state.runs].reverse().find(run=>run.id!==baseline.id&&sameScenarioScope(run,baseline))||null;if(!baseline||!right||!sameScenarioScope(baseline,right)){announce('不存在范围相同的第二个完整保留运行；请再次运行此场景后再比较已固定的基线。');return}const result=await api('/api/compare',{leftRunId:baseline.id,rightRunId:right.id,scenarioId:state.selected});$('comparison').innerHTML=`<p><strong>基线 ${esc(result.leftRunId)}</strong> → <strong>当前 ${esc(result.rightRunId)}</strong></p><p class="small">${esc(result.leftWorldHash)} → ${esc(result.rightWorldHash)}</p>${result.changes.length?`<table><thead><tr><th>路径</th><th>之前</th><th>之后</th></tr></thead><tbody>${result.changes.map(change=>`<tr><td>${esc(change.path)}</td><td><pre>${esc(JSON.stringify(change.before))}</pre></td><td><pre>${esc(JSON.stringify(change.after))}</pre></td></tr>`).join('')}</tbody></table>`:'<p>所选场景图没有结构变化。</p>'}`;announce(`基线对比已呈现，共 ${result.changes.length} 项结构变化。`)}).catch(error=>safeUiError('基线比较',error));
localizeStatic();
// The legacy console remains available for component diagnostics, but must
// not present its historical Stage/Gate labels as the product route.
document.querySelector('header .sub').textContent='组件诊断台 · 旧 Golden 场景核查，不是产品前端';
document.querySelector('.banner').innerHTML='<strong>证据边界。</strong> 这里仅核查保留的 Golden 组件场景；它不代表 Capability 1 的产品聊天链路，也不会替代 Owner dogfood。';
$('steps').setAttribute('aria-label','组件诊断步骤');
document.body.style.visibility='hidden';buildOwnerFirstShell();
const ownerBoundaryCopy=document.querySelector('#ownerFlow .owner-card:last-child p.small');
if(ownerBoundaryCopy)ownerBoundaryCopy.textContent='你的意见只保存在本机，帮助我们判断这些旧 Golden 组件是否值得保留；它不会改变任何记忆或产品路线。';
document.body.style.visibility='visible';refresh().then(renderAll).catch(error=>safeUiError('加载本地实验工作台',error));
</script></body></html>'''


# The owner-facing workbench intentionally lives outside the historic one-file
# Golden console.  ``/legacy`` keeps that console available for development,
# while ``/`` stays focused on one transparent scenario run at a time.
HTML = (Path(__file__).with_name("workbench.html")).read_text(encoding="utf-8")


class NextLabHTTPServer(HTTPServer):
    """Serialize Lab operations without letting an idle browser socket starve the server."""

    request_read_timeout_seconds = 2.0

    def get_request(self) -> tuple[socket.socket, Any]:
        request, client_address = super().get_request()
        request.settimeout(self.request_read_timeout_seconds)
        return request, client_address


class Handler(BaseHTTPRequestHandler):
    server_version = "MemoWeftNextLab/0"
    service: LabService
    bind_port: int
    instance_token: str

    def log_message(self, format: str, *args: Any) -> None:
        # The launcher owns request logs; avoid echoing request bodies to stdout.
        return

    def _origin_ok(self, *, mutation: bool = False) -> bool:
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        allowed_hosts = {f"127.0.0.1:{self.bind_port}", f"localhost:{self.bind_port}"}
        allowed_origins = {f"http://{item}" for item in allowed_hosts}
        return host in allowed_hosts and (origin in allowed_origins if mutation else (origin is None or origin in allowed_origins))

    def _send(self, status: HTTPStatus, payload: Any, content_type: str = "application/json; charset=utf-8") -> None:
        body = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _content_length(self) -> int:
        try:
            return int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return -1

    def _discard_bounded_body(self) -> None:
        """Drain small rejected POST bodies so Windows can deliver the response before close."""
        size = self._content_length()
        if 0 < size <= 65536:
            self.rfile.read(size)

    def do_GET(self) -> None:  # noqa: N802
        if not self._origin_ok():
            self._send(HTTPStatus.FORBIDDEN, {"error": "loopback Host/Origin validation failed"}); return
        routes: dict[str, Callable[[], Any]] = {
            "/api/scenarios": self.service.scenarios,
            "/api/runs": self.service.runs,
            "/api/reviews": self.service.reviews,
            "/api/memory-runs": self.service.memory_runs,
            "/api/memory-evaluations": self.service.memory_evaluations,
            "/api/memory-world": self.service.memory_world,
            "/api/chat-session": self.service.chat_session,
        }
        if self.path == "/":
            self._send(HTTPStatus.OK, HTML, "text/html; charset=utf-8")
        elif self.path == "/legacy":
            self._send(HTTPStatus.OK, LEGACY_HTML, "text/html; charset=utf-8")
        elif self.path == "/api/status":
            payload = self.service.status(); payload["serverIdentity"] = "MemoWeftNextLab/1"; payload["instanceToken"] = self.instance_token; self._send(HTTPStatus.OK, payload)
        elif self.path in routes:
            self._send(HTTPStatus.OK, routes[self.path]())
        else:
            self._send(HTTPStatus.NOT_FOUND, {"error": "unknown local route"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._origin_ok(mutation=True):
            self._discard_bounded_body()
            self._send(HTTPStatus.FORBIDDEN, {"error": "loopback Host/Origin validation failed"}); return
        routes: dict[str, Callable[[dict[str, Any]], Any]] = {
            "/api/run": lambda body: self.service.run(body.get("scenarioIds")),
            "/api/expand": self.service.expand,
            "/api/review": self.service.review,
            "/api/pin": self.service.pin,
            "/api/compare": self.service.compare,
            "/api/rerun": self.service.rerun,
            "/api/memory-runs": self.service.memory_run,
            "/api/memory-evaluations": self.service.memory_evaluation,
            "/api/memory-decisions": self.service.memory_decision,
            "/api/memory-queries": self.service.memory_query,
            "/api/memory-recalls": self.service.memory_recall,
            "/api/memory-corrections": self.service.memory_correction,
            "/api/chat-turns": self.service.chat_turn,
            "/api/adapter-memory-turns": self.service.adapter_memory_turns,
            "/api/adapter-legacy-memory-imports": self.service.adapter_legacy_memory_imports,
        }
        if self.path not in routes:
            self._discard_bounded_body()
            self._send(HTTPStatus.NOT_FOUND, {"error": "unknown allowlisted API route"}); return
        try:
            size = self._content_length()
            if size <= 0 or size > 65536:
                raise ValueError("request body must be between 1 and 65536 bytes")
            body = json.loads(self.rfile.read(size).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("JSON body must be an object")
            self._send(HTTPStatus.OK, routes[self.path](body))
        except KeyError as exc:
            self._send(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except (ValueError, json.JSONDecodeError) as exc:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Lab operation failed: {type(exc).__name__}"})


def main() -> None:
    parser = argparse.ArgumentParser(description="MemoWeft Next Lab loopback server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=7891, type=int)
    parser.add_argument("--state-dir", type=Path, default=REPO_ROOT / ".local" / "next-lab")
    parser.add_argument("--instance-token", required=True)
    args = parser.parse_args()
    if args.host != "127.0.0.1" or not 1 <= args.port <= 65535:
        parser.error("Next Lab only permits --host 127.0.0.1 and a valid port")
    Handler.service = LabService(args.state_dir)
    Handler.bind_port = args.port
    Handler.instance_token = args.instance_token
    server = NextLabHTTPServer((args.host, args.port), Handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
