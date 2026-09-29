"""Behavior checks for the submission's exchange, label and training gates.

All data is synthetic and temporary. No network, training or deployment.
"""
import importlib.util
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from engine import hunt, jevtrain as JT
from engine.learning_queue import LearningQueue

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def queue(tmp_path):
    return LearningQueue(tmp_path/'q.jsonl')

def collect(tmp_path,queue,attempts,judge):
    p=tmp_path/'hunt.json';p.write_text(json.dumps({'attempts':attempts}),encoding='utf-8')
    return JT.collect_from_hunt(p,queue,judge_fn=judge)

@pytest.mark.parametrize('method,body',[('GET',''),('POST','item=owned&action=update')])
def test_observed_exchange_is_identical_for_judge_queue_and_export(tmp_path,queue,method,body):
    e=hunt.exchange_evidence(method,'http://127.0.0.1:1/items?q=x',body,200,'HTTP_REAL_BODY')
    seen=[]
    collect(tmp_path,queue,[{'step':'attack_probe','exchange':e,'note':'PROBE_NOTE','markers_hit':['marker']}],lambda x:seen.append(x) or {})
    row=queue.load()[0]
    assert {k:row[k] for k in seen[0]}==seen[0]
    assert row['method']==method and row['body']==body and row['response']=='HTTP_REAL_BODY'
    assert 'PROBE_NOTE' not in row['response']
    JT.stamp_reviewer(queue,row['sample_id'],'unit-operator','synthetic marker observation')
    queue.approve(row['sample_id'])
    JT.export(queue,'test',out_root=tmp_path/'out')
    records=[json.loads(s) for n in ('train.jsonl','holdout.jsonl') for s in (tmp_path/'out'/n).read_text(encoding="utf-8").splitlines()]
    assert len(records)==1
    assert 'HTTP_REAL_BODY' in records[0]['messages'][1]['content']
    assert records[0]['meta']['review_state']=='approved'
    assert records[0]['meta']['label_review_state']=='auto_ok'

def test_historical_summary_and_failed_exchange_are_not_classified(tmp_path,queue):
    bad=hunt.exchange_evidence('GET','/x','',0,'','timeout')
    def no_call(_):raise AssertionError('incomplete exchange sent to model')
    r=collect(tmp_path,queue,[{'step':'old','status':200,'note':'not response'},{'step':'err','exchange':bad}],no_call)
    assert r['added']==0 and len(r['skipped'])==2

def test_post_producer_records_expanded_request_body():
    h=object.__new__(hunt.Hunt)
    h.base='http://127.0.0.1:1';h.scope=None;h.cls='sqli';h.instance='a';h.cookie=''
    h.requests_used=0;h.attempts=[];h.detector_markers=[];h._ctx={'TARGET':h.base,'ITEM':'owned'}
    h._budget_left=lambda:(True,'');h._queue_raw=lambda *a:'q-id'
    res=SimpleNamespace(response_text='POST_REAL_BODY',markers_hit=[],status=200)
    with patch('governance.payload_policy.assert_allowed'),patch('engine.replay.replay_payload',return_value=res):
        obs=h._fire_request('POST {{TARGET}}/items HTTP/1.1\nContent-Type: application/x-www-form-urlencoded\n\nitem={{ITEM}}','attack')
    assert obs['exchange']['method']=='POST' and obs['exchange']['body']=='item=owned'
    assert obs['exchange']['response']=='POST_REAL_BODY' and obs['exchange']['path']=='/items'

def test_label_review_is_separate_and_invalidates_old_approval(queue,tmp_path):
    e=hunt.exchange_evidence('GET','/x','',200,'no marker')
    collect(tmp_path,queue,[{'step':'attack_probe','exchange':e}],lambda _: {'verdict':'attack'})
    row=queue.load()[0];sid=row['sample_id']
    JT.stamp_reviewer(queue,sid,'unit-operator','sample admission only');queue.approve(sid)
    assert queue.load()[0]['label_review_state']=='needs_review'
    assert JT.export(queue,'before',out_root=tmp_path/'before')['training_ready'] is False
    changed=JT.review_label(queue,sid,'benign','unit-operator','synthetic known legitimate action')
    assert changed['review_state']=='pending' and changed['label_review_state']=='human_reviewed'
    assert changed['label_audit'][-1]['before']=='attack' and changed['label_audit'][-1]['after']=='benign'
    assert changed['agreement'] is False
    with pytest.raises(ValueError, match="revision"):
        queue.approve(sid, expected_label_revision=0)
    queue.approve(sid, reviewer="unit-operator", note="reapprove corrected label",
                  expected_label_revision=changed["label_revision"])
    JT.export(queue,'after',out_root=tmp_path/'after')
    records=[json.loads(s) for n in ('train.jsonl','holdout.jsonl') for s in (tmp_path/'after'/n).read_text(encoding="utf-8").splitlines()]
    assert records[0]['meta']['label_audit'][-1]['after']=='benign'

def test_new_family_split_does_not_depend_on_sample_id():
    with patch.object(JT,'lookup_split',return_value=None):
        assert len({JT._split_for('same-family',str(i)) for i in range(40)})==1

def load_gate():
    spec=importlib.util.spec_from_file_location('submission_train',ROOT/'bench/train/train_lora.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m._gate

def good_volume(tmp_path):
    d=tmp_path/'candidate';d.mkdir()
    files=[]
    for name,family in [('train.jsonl','train-family'),('holdout.jsonl','holdout-family')]:
        rows=[{'messages':[{'role':'assistant','content':label}], 'meta':{
            'family':family,'queue_sample_id':family+label,'review_state':'approved',
            'reviewer':'test-operator','reviewed_at':'test-time','label_review_state':'human_reviewed',
            'label_audit':[{'before':label,'after':label,'reviewer':'test-operator','evidence_note':'synthetic fixture'}]}}
            for label in ('attack','benign')]
        p=d/name;p.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
        files.append({'file':name,'rows':2,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
    m={'schema':'jevtrain-manifest-v1','training_ready':True,'training_blockers':[],
       'rows_needing_review':0,'files':files,'b_sealed_registry':{'loaded_families':2,
       'sources_missing':[],'sources':[{'path':'unit-fixture','status':'ok'}]}}
    (d/'MANIFEST.json').write_text(json.dumps(m),encoding='utf-8')
    return d,m

def test_strict_gate_accepts_complete_synthetic_volume(tmp_path):
    d,_=good_volume(tmp_path);load_gate()(d)

@pytest.mark.parametrize('change',['null','missing_hash','missing_registry','malformed','unapproved','wrong_hash','legacy'])
def test_strict_gate_rejects_incomplete_or_forged_volume(tmp_path,change):
    d,m=good_volume(tmp_path)
    if change=='null':m['training_ready']=None
    elif change=='missing_hash':del m['files'][0]['sha256']
    elif change=='missing_registry':del m['b_sealed_registry']
    elif change=='wrong_hash':m['files'][0]['sha256']='0'*64
    elif change=='unapproved':
        p=d/'train.jsonl';r=json.loads(p.read_text(encoding="utf-8").splitlines()[0]);r['meta']['review_state']='pending'
        p.write_text(json.dumps(r)+'\n',encoding='utf-8');m['files'][0].update(rows=1,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    (d/'MANIFEST.json').write_text('broken' if change=='malformed' else json.dumps(m),encoding='utf-8')
    with pytest.raises(SystemExit) as e:load_gate()(d,allow_legacy=change=='legacy')
    assert e.value.code==3
