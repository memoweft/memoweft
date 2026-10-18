"""Windows synthetic real-bridge memory curve; never opens an existing data home."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('output', type=Path)
parser.add_argument('--core-source', type=Path, default=root/'py/src')
parser.add_argument('--world-fixtures', action='store_true')
args = parser.parse_args()
if os.name != 'nt':
    parser.error('This probe records Windows private commit and working set.')
output = args.output.resolve()
output.parent.mkdir(parents=True, exist_ok=True)
if output.exists():
    parser.error('Use a new output file to keep each measurement separate.')
local = root/'.local'
local.mkdir(exist_ok=True)
env = {k:v for k,v in os.environ.items() if not k.startswith(('MEMOWEFT_', 'DSH_', 'HERMES_'))}
env.update(PYTHONPATH=str(Path(__file__).parent)+os.pathsep+str(args.core_source.resolve()), MEMOWEFT_SEMANTIC_ENABLED='0', BRIDGE_PROBE_OUTPUT=str(output), PYTHONIOENCODING='utf-8')
with tempfile.TemporaryDirectory(prefix='bridge-465-', dir=local) as home:
    with output.with_suffix('.stderr.log').open('w',encoding='utf-8') as err:
        p = subprocess.Popen([sys.executable,'-m','memoweft.integrations.dsh_bridge'],env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=err,text=True,encoding='utf-8')
        serial=0
        def call(method, params=None, sample=None):
            global serial
            serial+=1
            request=dict(protocol='memoweft.dsh_rpc',protocol_version=2,schema_version=1,request_id=sample or str(serial),method=method,params=params or {})
            p.stdin.write(json.dumps(request)+'\n'); p.stdin.flush()
            response=json.loads(p.stdout.readline())
            assert response['ok'], response
            return response
        def seed_world():
            # Fixture insertion only into this invocation's newly created home.
            # Source ingestion and all measured reads still use the real bridge.
            with sqlite3.connect(str(Path(home)/'memoweft/memoweft.sqlite3')) as db:
                for eid, sid, text, timestamp in db.execute('SELECT id,subject_id,raw_content,recorded_at FROM evidence').fetchall():
                    cid = 'synthetic-'+eid
                    db.execute("INSERT OR IGNORE INTO cognition (id,subject_id,content,content_type,formed_by,confidence,cred_status,created_at,updated_at) VALUES (?,?,?,'preference','stated',600,'limited',?,?)", (cid,sid,text,timestamp,timestamp))
                    db.execute("INSERT INTO cognition_evidence (cognition_id,evidence_id,relation) SELECT ?,?,'support' WHERE NOT EXISTS (SELECT 1 FROM cognition_evidence WHERE cognition_id=?)", (cid,eid,cid))
        try:
            call('initialize',dict(session_id='synthetic',dsh_home=home,platform='desktop',user_id='synthetic',auto_route=False,model_tier='local'))
            call('health',sample='sample-start')
            last=0
            for n in (32,128,512):
                for i in range(last,n):
                    payload=dict(schema_version=1,provider_name='memoweft',parent_session_id=f'conversation-{i}',result_session_id=f'conversation-{i}',mode='in_place',source_messages=[dict(role='user',content=f'Synthetic source {i}: '+('independent test fact. '*50),source_ref='source:0',message_id=f'message-{i}')])
                    digest=hashlib.sha256(json.dumps(payload,ensure_ascii=True,allow_nan=False,separators=(',',':'),sort_keys=True).encode()).hexdigest()
                    boundary=dict(payload,payload_hash=digest,event_id='weftmate-compression-boundary-v1:'+'b'*32+':'+digest)
                    call('ingest_boundary',dict(boundary=boundary))
                call('health',sample=f'sample-sources-{n}')
                if args.world_fixtures:
                    seed_world()
                for method, params in [('query_evidence',dict(operation='list')), ('query_interactions',dict(query='synthetic')), ('preview_recall_batch',dict(queries=['synthetic source','test fact']))]:
                    for j in range(10):
                        call(method, params)
                    call('health',sample=f'sample-{method}-{n}')
                last=n
            for n in (32,128,256):
                for j in range(n): call('query_evidence',dict(operation='list'))
                call('health',sample=f'sample-fixed-reads-plus-{n}')
            call('shutdown')
            assert p.wait(timeout=10) == 0
        finally:
            if p.poll() is None: p.kill(); p.wait()
for line in output.read_text().splitlines():
    row=json.loads(line)
    print(row['stage'],{k:round(row[k]/1048576,2) for k in ('commit','ws','heap','peak_heap')},flush=True)
