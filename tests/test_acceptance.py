"""Six assignment acceptance points and review checks; no historical dependencies."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import pytest
from knowledge_agent.tools.document_reader import DocumentStore
from conftest import ROOT,load,write,cli,action,direct,library,authenticated

STATES={'normal':'completed','conflict':'needs_clarification','out_of_scope':'insufficient_evidence',
    'paraphrase':'needs_clarification','follow_up':'completed','tool_failure':'completed'}

@pytest.mark.parametrize('scenario',list(STATES))
def test_six_required_scenarios(artifacts,scenario):
    payload=load(ROOT/f'examples/inputs/{scenario}.json')
    args=['--fail-tool','search:once'] if scenario=='tool_failure' else []
    value=cli(artifacts,scenario,payload,args,STATES[scenario]);authenticated(value)
    assert value['claims']
    if scenario=='normal':
        assert {c['doc_id'] for c in value['citations']}=={'D03'}
        assert 'IT 服务' in value['answer'] and '设备报修' in value['answer']
    if scenario in {'conflict','paraphrase'}:
        assert {c['doc_id'] for c in value['citations']}=={'D01','D02'}
        assert '30' in value['answer'] and '15' in value['answer'] and '请确认' in value['answer']
        assert value['gaps'] and value['trace'][-1]['action']=='ASK_CLARIFICATION'
    if scenario=='out_of_scope':assert not value['citations'] and value['gaps'] and all(c['kind']=='UNKNOWN' for c in value['claims'])
    if scenario=='follow_up':
        assert payload['history'] and any(c['doc_id']=='D05' for c in value['citations'])
        assert '邮箱' in value['trace'][0]['arguments']['query']
    if scenario=='tool_failure':
        first=value['trace'][0]
        assert first['action']=='SEARCH' and first['observation']['error']['code']=='simulated_tool_failure'
        assert value['trace'][1]['action']=='SEARCH' and value['trace'][1]['observation']['ok']

def test_single_D01_has_no_invented_conflict(artifacts):
    value=cli(artifacts,'single_d01',dict(question='报销期限多久？'),['--kb','data/variants/documents_d01_only.json'])
    authenticated(value)
    assert {c['doc_id'] for c in value['citations']}=={'D01'}
    assert '30 个自然日' in value['answer'] and '15 个自然日' not in value['answer'] and not value['gaps']

def test_document_replacement_changes_actual_answer_quote_and_action_arguments(artifacts):
    payload=load(ROOT/'examples/inputs/follow_up.json')
    before=cli(artifacts,'replacement_before',payload)
    after=cli(artifacts,'replacement_after',payload,['--kb','examples/variants/documents_v2.json'])
    authenticated(before);authenticated(after)
    quote=lambda value:next(c['quote'] for c in value['citations'] if c['doc_id']=='D05')
    document=lambda value:next(s['observation']['result']['document'] for s in value['trace'] if s['action']=='READ' and s['arguments']['doc_id']=='D05')
    assert before['knowledge_base_sha256']!=after['knowledge_base_sha256']
    assert before['answer']!=after['answer'] and quote(before)!=quote(after)
    assert '移动端暂不支持' in after['answer']
    assert document(before)!=document(after) and before['trace'][-1]['arguments']!=after['trace'][-1]['arguments']
    write(artifacts/'document-replacement-comparison.json',dict(mode='mock',new_real_api_calls=0,
        same_input=True,answer_changed=True,citation_changed=True,read_observation_changed=True,terminal_action_arguments_changed=True,
        before_actions=[s['action'] for s in before['trace']],after_actions=[s['action'] for s in after['trace']],
        action_sequence_changed=[s['action'] for s in before['trace']]!=[s['action'] for s in after['trace']],
        note='Same source locations may retain the same tool sequence. No claim of real-model replacement or changed action count.'))

@pytest.mark.parametrize('name,args,scenario',[
    ('step_limit',['--max-steps','2'],'conflict'),('tool_limit',['--max-tool-calls','1'],'normal'),
    ('persistent_read_failure',['--fail-tool','read:always'],'normal'),
])
def test_limits_and_unrecoverable_failure_are_explicit(artifacts,name,args,scenario):
    state='tool_failed' if name=='persistent_read_failure' else 'max_steps_reached'
    value=cli(artifacts,name,load(ROOT/f'examples/inputs/{scenario}.json'),args,state)
    assert not value['citations'] and value['gaps']
    if name=='step_limit':assert len(value['trace'])==2
    if name=='tool_limit':assert any(s['observation'].get('error',{}).get('executed') is False for s in value['trace'])

@pytest.mark.parametrize('bad',['unread','invented_quote','wrong_version','unsupported_claim'])
def test_invalid_citations_and_unsupported_answer_never_reach_user(artifacts,bad):
    store=DocumentStore(ROOT/'data/documents.json');candidate=direct(store)
    if bad=='invented_quote':candidate['arguments']['citations'][0]['quote']='公司对所有维修设备提供十年保修。'
    if bad=='wrong_version':candidate['arguments']['citations'][0]['version_date']='1999-01-01'
    if bad=='unsupported_claim':
        candidate['arguments']['answer']='公司对所有维修设备提供十年保修。'
        candidate['arguments']['claims'][0]['text']=candidate['arguments']['answer']
    steps=[] if bad=='unread' else [action('READ',doc_id='D03')]
    value,_=library(artifacts,'invalid_'+bad,dict(question='设备报修的规定是什么？'),steps+[candidate],store)
    assert value['status'] not in {'completed','needs_clarification'} and not value['citations']
    assert value['trace'][-1]['observation']['ok'] is False

def test_validator_feedback_allows_model_to_repair_its_next_action(artifacts):
    store=DocumentStore(ROOT/'data/documents.json');good=direct(store);bad=deepcopy(good)
    bad['arguments']['citations'][0]['quote']='虚构的报修政策，不存在于原文。'
    value,model=library(artifacts,'citation_repair',dict(question='电脑故障怎么报修？'),[action('READ',doc_id='D03'),bad,good],store)
    assert value['status']=='completed' and len(model.contexts)==3
    assert model.contexts[-1]['observations'][-1]['ok'] is False
    assert value['trace'][1]['observation']['error']['submitted_to_user'] is False
    authenticated(value)

@pytest.mark.parametrize('other_section',[False,True])
def test_section_read_grants_only_the_returned_fragment(artifacts,other_section):
    store=DocumentStore(ROOT/'data/documents.json');doc=store.documents['D04']
    read_section=doc.sections[0];quoted=doc.sections[1] if other_section else read_section
    proposal=action('ANSWER',answer=quoted.content,status='completed',gaps=[],
        citations=[dict(doc_id=doc.doc_id,version_date=doc.version_date,quote=quoted.content)],
        claims=[dict(text=quoted.content,kind='DIRECT',citation_indices=[0])])
    value,_=library(artifacts,'section_unread' if other_section else 'section_read',dict(question='公司邮箱如何找回密码？'),
        [action('READ',doc_id=doc.doc_id,section_id=read_section.section_id),proposal],store)
    if other_section:assert value['status']!='completed' and not value['citations']
    else:assert value['status']=='completed';authenticated(value)

@pytest.mark.parametrize('order',['empty_search_then_requery','read_then_repeated_search'])
def test_loop_does_not_force_one_business_action_sequence(artifacts,order):
    store=DocumentStore(ROOT/'data/documents.json')
    if order=='empty_search_then_requery':
        steps=[action('SEARCH',query='火星基地不存在的制度'),action('SEARCH',query='设备故障报修'),action('READ',doc_id='D03'),direct(store)]
    else:steps=[action('READ',doc_id='D03'),action('SEARCH',query='更多设备报修信息'),action('SEARCH',query='设备故障报修'),direct(store)]
    value,model=library(artifacts,order,dict(question='设备如何报修？'),steps,store)
    assert value['status']=='completed' and [s['action'] for s in value['trace']]==[s['action'] for s in steps]
    assert len(model.contexts[-1]['observations'])==3
    if order=='empty_search_then_requery':
        assert model.contexts[1]['observations'][0]['result']['results']==[]
        assert value['trace'][1]['arguments']['query']!=value['trace'][0]['arguments']['query']
    authenticated(value)

def test_history_enters_model_context_without_becoming_policy_evidence(artifacts):
    store=DocumentStore(ROOT/'data/documents.json')
    request=dict(question='那怎么报修？',history=[dict(role='user',content='电脑无法启动。'),dict(role='assistant',content='还需要查阅资料。')])
    value,model=library(artifacts,'history_context',request,[action('READ',doc_id='D03'),direct(store)],store)
    assert value['status']=='completed' and model.contexts[0]['history']==request['history']
    assert {c['doc_id'] for c in value['citations']}=={'D03'}

@pytest.mark.parametrize('name',['normal','conflict','out_of_scope','paraphrase_failed'])
def test_saved_real_records_are_actual_distinct_paths_and_keep_failure(name):
    folder=ROOT/'examples/real'/name;saved=load(folder/'output.json');value=saved['result']
    assert value['model_mode']=='real' and value['trace']==load(folder/'trace.json')['steps']
    assert saved['input']==load(folder/'input.json')
    assert all(s['model_metadata'].get('response_id') for s in value['trace'])
    assert len({s['model_metadata']['response_id'] for s in value['trace']})==len(value['trace'])
    authenticated(value)
    if name=='out_of_scope':
        searches=[s for s in value['trace'] if s['action']=='SEARCH']
        assert searches[0]['observation']['result']['results']==[]
        assert len({s['arguments']['query'] for s in searches})>1 and value['status']=='insufficient_evidence'
    if name=='conflict':assert value['status']=='needs_clarification' and '请确认' in value['answer']
    if name=='paraphrase_failed':assert value['status']=='max_steps_reached'

def test_retrieval_baseline_report_is_recomputable_offline(artifacts):
    destination=artifacts/'retrieval';destination.mkdir()
    command=[sys.executable,str(ROOT/'scripts/offline_python.py'),'--audit-report',str(destination/'network.json'),
        '--module','knowledge_agent','--','--evaluate-retrieval','--output',str(destination/'metrics.json')]
    with (destination/'stdout.txt').open('x',encoding='utf-8') as stream:result=subprocess.run(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
    value=load(destination/'metrics.json')
    assert result.returncode==0 and value['case_count']==12 and set(value['systems'])=={'baseline_bm25','hybrid'}
    assert load(destination/'network.json')['external_network_attempts']==[]

def test_default_D_retrieval_comparison_uses_frozen_public_diagnostic_labels(artifacts):
    from knowledge_agent.retrieval.original import OriginalHybridRetriever
    from knowledge_agent.retrieval.retriever import HybridRetriever
    from knowledge_agent.retrieval.generalized import GeneralizingRetriever
    from knowledge_agent.retrieval.metrics_v4 import evaluate
    store=DocumentStore(ROOT/'data/documents.json')
    dataset=ROOT/'examples/retrieval/diagnostic-24.json'
    metadata=load(ROOT/'examples/retrieval/diagnostic-24-meta.json')
    assert hashlib.sha256(dataset.read_bytes()).hexdigest()==metadata['dataset_sha256']
    cases=load(dataset);assert len(cases)==24
    class BM25:
        def __init__(self):self.base=OriginalHybridRetriever(store)
        def search(self,query,top_k=3):return self.base.search(query,top_k,baseline=True)
    systems={'A_original_bm25':BM25(),'B_original_hybrid':OriginalHybridRetriever(store),
        'C_previous_optimized':HybridRetriever(store),'D_current_default':GeneralizingRetriever(store)}
    report=dict(mode='offline_public_diagnostic_replay',new_real_api_calls=0,dataset=metadata,knowledge_base_sha256=store.sha256,
        systems={name:evaluate(retriever,cases) for name,retriever in systems.items()})
    for system in report['systems'].values():
        assert 0<=system['metrics']['recall_at_3']<=1
        assert system['metrics']['positive_queries']==17 and system['metrics']['negative_queries']==7
        for row in system['cases']:
            for hit in row['hits']:assert hit['snippet'] in store.documents[hit['doc_id']].content
    write(artifacts/'retrieval-default-d/metrics.json',report)

def test_submission_manifest_and_unchanged_runtime_sources():
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    for name,digest in load(ROOT/'MANIFEST.json')['files'].items():assert sha(ROOT/name)==digest
    for name,digest in load(ROOT/'examples/evidence/runtime-freeze.json')['source_files'].items():assert sha(ROOT/name)==digest

def test_review_document_links_resolve_inside_submission():
    for document in [ROOT/'README.md',*(ROOT/'docs').glob('*.md')]:
        for href in re.findall(r'\]\(([^)]+)\)',document.read_text(encoding='utf-8')):
            if re.match(r'https?://|#',href):continue
            target=(document.parent/href.split('#')[0]).resolve()
            assert target.is_relative_to(ROOT) and target.exists(),(document,href)
