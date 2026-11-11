"""Execute the canonical generated bootstrap instead of forging its receipts."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which("node") is None,reason="Node.js required")
def test_executed_bootstrap_produces_individual_receipt_lines_and_runs_once():
    runtime=Path(__file__).resolve().parents[1]/"src/core/client/login_delivery.cjs"
    script=r'''
const fs=require('fs'),vm=require('vm'),zlib=require('zlib');
const source=fs.readFileSync(process.argv[1],'utf8');
const start=source.indexOf('function bootstrap('),end=source.indexOf('function compose(',start);
const render=vm.runInNewContext(source.slice(start,end)+'; bootstrap',{Buffer});
const providers=[['one',[]],['two',['one']]].map(([id,requires])=>({id,requires,
 encoded:zlib.deflateSync(Buffer.from("_evejs_context['seen'].append('"+id+"')\nimport sys\nsys.stdout.write('unframed "+id+"')\n")).toString('base64')}));
process.stdout.write(render(providers,'execution-probe'));
'''
    completed=subprocess.run([shutil.which("node"),"-e",script,str(runtime)],capture_output=True,text=True,encoding="utf-8",timeout=15,check=True)
    context={"seen":[]}
    output=io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(compile(completed.stdout,"<actual-login-bootstrap>","exec"),{"_evejs_context":context})
    assert context["seen"]==["one","two"]
    assert output.getvalue().splitlines()==[
        "unframed one",
        "EVEJS_LOGIN:v1:execution-probe:one:OK",
        "unframed two",
        "EVEJS_LOGIN:v1:execution-probe:two:OK",
    ]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required")
def test_native_marshal_unicode_receipts_and_strict_negative_controls(tmp_path):
    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root/"tests/fixtures/native-codec-261108.1/server", tmp_path/"server")
    script = r'''
const assert=require('node:assert/strict');
const api=require(process.argv[1]),codec=require(process.argv[2]+'/server/src/network/tcp/utils/marshal.js');
for(const kind of ['wstring','bytes','string','wrong-nonce','duplicate','wrong-type','oversized']){
 class Host{};Host.prototype._compile=function(){};Host._cache={};
 class Session{constructor(){this.userId=1;this.frames=[];this.replies=0;}
  _sendPacket(packet){this.frames.push(packet);}_handleFuncResult(){this.replies++;return 77;}}
 const success=[],failure=[],runtime=api.createRuntime(process.argv[2],{codec,moduleHost:Host,logger:{error(){}}});
 runtime.register({id:'probe',source:'pass\n',onReceipt:()=>success.push(true),onFailure:()=>failure.push(true)});
 runtime.attach(Session);
 const expression=Buffer.from('77'),envelope=Buffer.alloc(expression.length+5);
 envelope[0]=0x74;envelope.writeUInt32LE(expression.length,1);expression.copy(envelope,5);
 const session=new Session();
 session._sendPacket(codec.encodePacket(['',[envelope,0],{type:'dict',entries:[]},
  {type:'dict',entries:[['challenge_responsehash','unchanged']]}]));
 const encoded=codec.marshalDecodeExact(session.frames[0].subarray(4))[1][0].subarray(5).toString('latin1');
 const bootstrap=Buffer.from(encoded.match(/b64decode\("([A-Za-z0-9+/=]+)"\)/)[1],'base64').toString('utf8');
 const nonce=bootstrap.match(/EVEJS_LOGIN:v1:([a-f0-9]{32}):/)[1];
 let output='EVEJS_LOGIN:v1:'+nonce+':probe:OK\n';
 if(kind==='wrong-nonce')output=output.replace(nonce,'0'.repeat(32));
 if(kind==='duplicate')output+=output;
 if(kind==='oversized')output='x'.repeat(api.MAX_EXPRESSION)+output;
 let value=kind==='bytes'?Buffer.from(output):kind==='string'?output:{type:kind==='wrong-type'?'list':'wstring',value:output};
 // Actual codec round-trip for the retail Unicode shape, rather than casting
 // its contents to a Node Buffer before invoking the production observer.
 let reply=kind==='wrong-type'?['challenge',value,null]:codec.marshalDecodeExact(codec.marshalEncode(['challenge',value,null]));
 assert.equal(session._handleFuncResult(reply),77);
 assert.equal(session.replies,1);
 const expected=['wstring','bytes','string'].includes(kind);
 assert.equal(success.length,expected?1:0,kind);assert.equal(failure.length,expected?0:1,kind);
 session._handleFuncResult(reply);assert.equal(success.length,expected?1:0);assert.equal(failure.length,expected?0:1);
}
process.stdout.write('Seven actual-codec receipt cases passed');
'''
    result = subprocess.run([shutil.which("node"), "-e", script, str(root/"src/core/client/login_delivery.cjs"), str(tmp_path)],
        capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr
    assert "Seven actual-codec" in result.stdout
