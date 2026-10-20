"""Tuning principles reach the real formation route; source detail stays exact."""
import json
from pathlib import Path
import sqlite3

from memoweft.integrations.hermes.batch_adapter import HermesBatchAdapterProcessor
from memoweft.integrations.hermes.world_worker import WorldJobWorker
from test_hermes_batch_adapter_v2 import _set_evidence
from test_hermes_world_worker import MutableClock, _initialize_database, _insert_job, _job, _policy


def test_concrete_source_and_change_principles_reach_formation(tmp_path: Path) -> None:
    path=tmp_path/'detail.sqlite3'
    clock=MutableClock()
    _initialize_database(path)
    _insert_job(path,clock)
    clauses=('I earn 4300 credits each month.', 'I work four days per week because I study on Fridays.')
    raw=' '.join(clauses)
    _set_evidence(path,'evidence-1',raw)
    prompts: list[str]=[]
    def route(messages: list[dict[str,str]], *, session_id: str) -> dict[str,object]:
        del session_id
        prompts.append(messages[0]['content'])
        return {'content':json.dumps({'schema_version':7,'result':'cognitions','cognitions':[
            {'action':'form','target':'owner_self','statement_kind':'attribute','formed_by':'stated',
             'proposition':clause,'supports':[{'evidence_id':'evidence-1','start':raw.index(clause),'end':raw.index(clause)+len(clause)}]}
            for clause in clauses]}),'model':'scripted'}
    worker=WorldJobWorker(path,processor=HermesBatchAdapterProcessor(str(path),route,clock=clock,lang='en'),policy=_policy(),clock=clock)
    assert worker.run_until_quiescent()==1
    assert _job(path)['state']=='applied'
    with sqlite3.connect(path) as db:
        assert {r[0] for r in db.execute('SELECT content FROM cognition')}==set(clauses)
    assert 'Do not replace concrete details with a broader category' in prompts[0]
    assert 'old state, new state, change time and reason' in prompts[0]
