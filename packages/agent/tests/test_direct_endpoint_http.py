"""Exercise runtime commands against loopback HTTP using fake CLI binaries."""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from anygarden_agent.runtime.execution.contracts import Invocation, SessionScope
from anygarden_agent.runtime.execution.codex import CodexRuntime
from anygarden_agent.runtime.execution.pi import PiRuntime
from anygarden_agent.runtime.execution.endpoint import DirectEndpoint, endpoint_environment


@pytest.fixture
def endpoint_server():
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((self.path,self.headers.get('Authorization'),body))
            self.send_response(200);self.end_headers();self.wfile.write(b'{"text":"local answer"}')
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield f'http://127.0.0.1:{server.server_port}/v1',requests
    server.shutdown();thread.join();server.server_close()


@pytest.mark.parametrize('engine,protocol',[('codex-cli','responses'),('pi-cli','responses'),('pi-cli','chat-completions')])
@pytest.mark.parametrize('authenticated',[False,True])
async def test_selected_endpoint_model_and_auth_reach_loopback(tmp_path,endpoint_server,engine,protocol,authenticated,monkeypatch):
    url,requests=endpoint_server
    endpoint=DirectEndpoint('my-local','local-model',url,protocol,'ref' if authenticated else None,1 if authenticated else 0)
    home=tmp_path/'home';home.mkdir();workspace=tmp_path/'workspace';workspace.mkdir()
    executable=tmp_path/'fake-cli'
    executable.write_text(f'#!{sys.executable}\n'+r'''
import sys,os,json,tomllib,urllib.request
from pathlib import Path
args=sys.argv[1:]
pi='--print' in args or os.environ['TEST_ENGINE']=='pi-cli'
if args==['--version']:
    print('0.85.1' if pi else 'codex-cli 0.155.1');sys.exit(0)
sys.stdin.read()
if pi:
    provider=args[args.index('--provider')+1];model=args[args.index('--model')+1]
    entry=json.loads((Path(os.environ['PI_CODING_AGENT_DIR'])/'models.json').read_text())['providers'][provider]
    base=entry['baseUrl'];protocol=entry['api'];key=os.environ[entry['apiKey'][1:]]
    route='/responses' if protocol=='openai-responses' else '/chat/completions'
else:
    config={}
    for i,value in enumerate(args):
        if value=='-c' and args[i+1].startswith(('model_provider=', 'model_providers.')):
            config.update({args[i+1].split('=',1)[0]:tomllib.loads('value='+args[i+1].split('=',1)[1])['value']})
    base=config['model_providers.ag_direct.base_url'];route='/responses';model=args[args.index('-m')+1]
    key=os.environ.get(config.get('model_providers.ag_direct.env_key',''))
headers={'Content-Type':'application/json'}
if key:headers['Authorization']='Bearer '+key
request=urllib.request.Request(base.rstrip('/')+route,json.dumps({'model':model}).encode(),headers)
with urllib.request.urlopen(request,timeout=2) as r:answer=json.load(r)['text']
if pi:
    print(json.dumps({'type':'session','id':'direct-session'}))
    print(json.dumps({'type':'message_end','message':{'role':'assistant','content':[{'type':'text','text':answer}],'stopReason':'stop','usage':{'input':1,'output':1}}}))
    print(json.dumps({'type':'agent_settled'}))
else:
    print(json.dumps({'type':'thread.started','thread_id':'direct-session'}))
    print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}))
    Path(args[args.index('-o')+1]).write_text(answer)
''')
    executable.chmod(0o700)
    env=endpoint_environment(endpoint,'fake-only-token' if authenticated else None)
    env['TEST_ENGINE']=engine
    invocation=Invocation('direct-1',SessionScope('n','a','n','r',None,'w',1,1,engine,'0.85.1' if engine=='pi-cli' else '0.155.1'),
        'hello',workspace,home,model=endpoint.model,provider=endpoint.provider,environment=env,endpoint=endpoint)
    monkeypatch.setenv('AG_DIRECT_API_KEY','ambient-must-not-be-used')
    runtime=PiRuntime(executable) if engine=='pi-cli' else CodexRuntime(executable)
    result=await runtime.run(invocation,None,lambda *_:None,lambda *_:None,lambda:True)
    assert result.outcome=='succeeded',result
    assert len(requests)==1
    path,key,body=requests[0]
    assert path=='/v1/'+('responses' if protocol=='responses' else 'chat/completions')
    assert body['model']=='local-model'
    assert key==('Bearer fake-only-token' if authenticated else ('Bearer ag-keyless-local' if engine=='pi-cli' else None))
    assert os.environ['AG_DIRECT_API_KEY']=='ambient-must-not-be-used'
    for file in home.rglob('*'):
        if file.is_file():assert 'fake-only-token' not in file.read_text()
