"""Self-contained offline test support; artifacts are saved below examples/."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess
import sys
from uuid import uuid4
import pytest

from knowledge_agent.agent import KnowledgeAgent
from knowledge_agent.config import AgentConfig
from knowledge_agent.llm.mock_llm import MockLLM
from knowledge_agent.schemas import QuestionInput
from knowledge_agent.tools.document_reader import DocumentStore
from knowledge_agent.tools.document_search import DocumentTools

ROOT=Path(__file__).resolve().parents[1]
REPORTS=[]
def load(path):return json.loads(path.read_text(encoding='utf-8'))
def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)

def pytest_addoption(parser):parser.addoption('--artifacts-dir',default=None,help='New output directory, normally below examples/.')
def pytest_configure(config):
    chosen=config.getoption('--artifacts-dir')
    directory=(Path(chosen) if chosen else ROOT/'examples/test-runs'/('run-'+uuid4().hex[:8])).resolve()
    if (directory/'summary.json').exists():raise pytest.UsageError('Use a new artifacts directory; saved results are not overwritten.')
    directory.mkdir(parents=True,exist_ok=True)
    config._acceptance_directory=directory
def pytest_runtest_logreport(report):
    if report.when=='call' or report.failed:REPORTS.append(dict(nodeid=report.nodeid,outcome=report.outcome,phase=report.when))
def pytest_sessionfinish(session,exitstatus):
    directory=session.config._acceptance_directory
    counts={key:sum(r['outcome']==key for r in REPORTS) for key in ['passed','failed','skipped']}
    write(directory/'summary.json',dict(scope='standalone_submission_tests',exit_code=int(exitstatus),collected=session.testscollected,
        **counts,new_real_api_calls=0,records=REPORTS,
        boundary='Mock/scripted fixtures and read-only saved Real records; this is not new real-model validation.'))

@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def denied(*args,**kwargs):raise AssertionError('Network forbidden in submission tests')
    monkeypatch.setenv('OPENAI_API_KEY','');monkeypatch.setenv('OPENAI_MODEL','')
    for name in ['connect','connect_ex','sendto']:monkeypatch.setattr(socket.socket,name,denied)
    monkeypatch.setattr(socket,'getaddrinfo',denied)

@pytest.fixture(scope='session')
def artifacts(pytestconfig):return pytestconfig._acceptance_directory

def cli(artifacts,name,payload,args=(),expected='completed'):
    folder=artifacts/name;folder.mkdir()
    write(folder/'input.json',payload)
    command=[sys.executable,'-X','utf8',str(ROOT/'scripts/offline_python.py'),'--audit-report',str(folder/'network.json'),
        '--module','knowledge_agent','--','--mode','mock','--answer-quality','enforce','--input',str(folder/'input.json'),
        *args,'--output',str(folder/'output.json'),'--trace',str(folder/'trace.json')]
    write(folder/'run-config.json',dict(mode='mock',arguments=list(args),command=command,new_real_api_calls=0))
    with (folder/'stdout.txt').open('x',encoding='utf-8') as stream:
        process=subprocess.run(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
    assert process.returncode==0,folder/'stdout.txt'
    result=load(folder/'output.json')
    assert result['status']==expected and result['model_mode']=='mock'
    assert result['trace']==load(folder/'trace.json')
    assert load(folder/'network.json')['external_network_attempts']==[]
    return result

class ScriptedModel:
    mode,name='mock','scripted-offline-contract'
    def __init__(self,actions):self.actions=iter(deepcopy(actions));self.contexts=[]
    def decide(self,context):self.contexts.append(deepcopy(context));return next(self.actions)
def action(name,**arguments):return dict(action=name,arguments=arguments,decision_summary='离线脚本化测试动作，不是真实模型新决策。')
def direct(store,doc_id='D03',**changes):
    doc=store.documents[doc_id]
    result=action('ANSWER',answer=doc.content,status='completed',gaps=[],
        citations=[dict(doc_id=doc.doc_id,version_date=doc.version_date,quote=doc.content)],
        claims=[dict(text=doc.content,kind='DIRECT',citation_indices=[0])])
    result['arguments'].update(changes)
    return result
def library(artifacts,name,request,actions,store=None):
    store=store or DocumentStore(ROOT/'data/documents.json')
    model=ScriptedModel(actions)
    agent=KnowledgeAgent(model,DocumentTools(store),AgentConfig(len(actions),6,True,'enforce'))
    value=agent.run(QuestionInput.model_validate(request)).model_dump(mode='json')
    folder=artifacts/name;folder.mkdir()
    write(folder/'input.json',request);write(folder/'output.json',value);write(folder/'trace.json',value['trace'])
    write(folder/'run-config.json',dict(mode='scripted_offline',new_real_api_calls=0,strict_claims=True,quality='enforce'))
    return value,model

def authenticated(value):
    reads=[s['observation']['result']['document'] for s in value['trace'] if s['action']=='READ' and s['observation']['ok']]
    for citation in value['citations']:
        assert any(d['doc_id']==citation['doc_id'] and d['version_date']==citation['version_date'] and citation['quote'] in d['content'] for d in reads)
