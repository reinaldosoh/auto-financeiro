#!/usr/bin/env python3
"""
Console local para testar a cobrança de royalties na VPS (sem credenciais no código).

    python3 scripts/console_royalties.py
    → abre http://127.0.0.1:8765 no navegador

Na tela você informa: URL da VPS, X-API-Key, e-mail e senha da matriz, e depois o código 2FA.
Este script só repassa /financeiro/* para a VPS (a VPS não libera CORS para o navegador).
Nada é gravado em disco.
"""

import json
import sys
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORTA = 8765
VPS_PADRAO = "https://reinaldo-automachine.sw5bxa.easypanel.host"

PAGINA = r"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Royalties – teste</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--tx:#1d2330;--mut:#667085;--bd:#e3e6ea;--pri:#1f6feb;--ok:#127a3e;--err:#b42318}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,sans-serif;background:var(--bg);color:var(--tx)}
main{max-width:980px;margin:0 auto;padding:24px 16px}h1{font-size:22px;margin:0 0 4px}p.sub{color:var(--mut);margin:0 0 20px}
section{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:18px;margin-bottom:16px}
h2{font-size:16px;margin:0 0 12px}label{display:block;font-size:13px;color:var(--mut);margin:10px 0 4px}
input,select{width:100%;padding:9px 10px;border:1px solid var(--bd);border-radius:7px;font:inherit}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}@media(max-width:640px){.row{grid-template-columns:1fr}}
button{margin-top:14px;background:var(--pri);color:#fff;border:0;border-radius:7px;padding:10px 16px;font:inherit;cursor:pointer}
button:disabled{opacity:.5;cursor:default}.msg{margin-top:10px;font-size:14px}.ok{color:var(--ok)}.err{color:var(--err)}
.code{font-size:28px;letter-spacing:8px;text-align:center;max-width:220px}
.lista{max-height:260px;overflow:auto;border:1px solid var(--bd);border-radius:7px;padding:6px 10px;margin-top:6px}
.lista label{display:flex;gap:8px;align-items:center;color:var(--tx);margin:4px 0}.lista input{width:auto}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:10px}th,td{text-align:right;padding:6px;border-bottom:1px solid var(--bd)}
th:first-child,td:first-child{text-align:left}.hidden{display:none}pre{white-space:pre-wrap;font-size:12px;background:var(--bg);padding:10px;border-radius:7px;max-height:300px;overflow:auto}
</style></head><body><main>
<h1>Cobrança de royalties · Cenários 1 e 2</h1><p class="sub">Teste da API da VPS. Nada é salvo: credenciais ficam só nesta aba.</p>

<section id="s1"><h2>1. Conexão e login da matriz</h2>
<div class="row"><div><label>URL da VPS</label><input id="vps"></div>
<div><label>X-API-Key da VPS</label><input id="key" type="password" autocomplete="off"></div></div>
<div class="row"><div><label>E-mail da matriz (Machine)</label><input id="email" type="email" autocomplete="username"></div>
<div><label>Senha</label><input id="senha" type="password" autocomplete="current-password"></div></div>
<button id="bLogin">Entrar</button><div id="m1" class="msg"></div></section>

<section id="s2" class="hidden"><h2>2. Código do autenticador</h2>
<p class="sub">Digite o código de 6 dígitos do app autenticador do responsável pela conta.</p>
<input id="codigo" class="code" inputmode="numeric" maxlength="7" autocomplete="one-time-code">
<br><button id="b2fa">Confirmar código</button><div id="m2" class="msg"></div></section>

<section id="s3" class="hidden"><h2>3. Rodar</h2>
<label>Cenário da rodada (uma rodada = um cenário)</label>
<select id="cenario"><option value="">Selecione o cenário…</option>
<option value="cenario_01">Cenário 1 – % sobre o transacionado (ex.: Alfenas 15%)</option>
<option value="cenario_02">Cenário 2 – R$ fixo por corrida (ex.: Passos R$ 2,50)</option></select>
<div class="row"><div><label>Competência</label><input id="mes" type="month"></div>
<div><label>Royalty Ubiz da cidade (%) – usado no teste</label><input id="pct" type="number" step="0.01" value="10"></div></div>
<div class="row"><div id="campoSeg"><label>Seguro app por corrida (R$) – só cenário 1</label><input id="seg" type="number" step="0.01" value="0.15"></div>
<div><label>Tarifa de boleto (R$)</label><input id="tar" type="number" step="0.01" value="3.50"></div></div>
<label>Cidades <a href="#" id="todas">marcar todas</a> · <a href="#" id="nenhuma">nenhuma</a></label><div id="lista" class="lista"></div>
<button id="bRodar">Rodar (simulação, não grava)</button><div id="m3" class="msg"></div>
<table id="tab" class="hidden"><thead><tr><th>Cidade</th><th>Status</th><th>Ganhos gerais</th><th>Taxa central</th><th>Saldo 01</th><th>Boleto Ubiz<br><small>(Machine)</small></th><th>Seguro app</th><th>Base</th><th>Royalty</th><th>Valor final</th></tr></thead><tbody></tbody></table>
<details><summary>Resposta completa (JSON)</summary><pre id="json"></pre></details></section>
</main><script>
const $=id=>document.getElementById(id);const st={login_id:null,token:null};
const brl=v=>v==null?'—':Number(v).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});
try{$('vps').value=sessionStorage.getItem('vps')||'__VPS__';$('key').value=sessionStorage.getItem('key')||''}catch(e){$('vps').value='__VPS__'}
const d=new Date();d.setMonth(d.getMonth()-1);$('mes').value=d.toISOString().slice(0,7);
function msg(el,t,ok){el.textContent=t;el.className='msg '+(ok?'ok':'err')}
async function api(metodo,caminho,corpo){
  try{sessionStorage.setItem('vps',$('vps').value);sessionStorage.setItem('key',$('key').value)}catch(e){}
  const h={'Content-Type':'application/json','X-Vps':$('vps').value.trim(),'X-API-Key':$('key').value.trim()};
  if(st.token)h['X-Session-Token']=st.token;
  const r=await fetch('/proxy'+caminho,{method:metodo,headers:h,body:corpo?JSON.stringify(corpo):undefined});
  let j;try{j=await r.json()}catch(e){j={detail:{mensagem:'Resposta inválida (HTTP '+r.status+')'}}}
  if(!r.ok){const d=j.detail;let m=(d&&(d.mensagem||d))||('HTTP '+r.status);
    if(r.status===401&&m==='Unauthorized')m='X-API-Key da VPS inválida.';
    if(r.status===404&&!(d&&d.codigo))m='A VPS ainda não tem a rota /financeiro: falta publicar a versão nova (sha-4166c98).';
    throw new Error(typeof m==='string'?m:JSON.stringify(m))}return j}
function mostrarCentrais(cs){$('lista').innerHTML=cs.map(c=>`<label><input type="checkbox" value="${c.bandeira_id}"> ${c.nome} <small style="color:#667085">#${c.bandeira_id}${c.atual?' · atual':''}</small></label>`).join('');
  $('s3').classList.remove('hidden')}
function autenticado(j){st.token=j.session_token;msg($('m2'),'Autenticado.',true);$('s2').classList.add('hidden');mostrarCentrais(j.centrais||[]);msg($('m1'),'Logado como '+j.email,true)}
$('bLogin').onclick=async()=>{$('bLogin').disabled=true;msg($('m1'),'Entrando…',true);
  try{const j=await api('POST','/financeiro/login',{email:$('email').value.trim(),senha:$('senha').value});$('senha').value='';
    if(j.status==='aguardando_2fa'){st.login_id=j.login_id;msg($('m1'),'Senha OK. Falta o código 2FA (5 min).',true);$('s2').classList.remove('hidden');$('codigo').focus()}else autenticado(j)}
  catch(e){msg($('m1'),e.message)}finally{$('bLogin').disabled=false}};
$('b2fa').onclick=async()=>{$('b2fa').disabled=true;
  try{autenticado(await api('POST','/financeiro/login/2fa',{login_id:st.login_id,codigo:$('codigo').value}))}
  catch(e){msg($('m2'),e.message)}finally{$('b2fa').disabled=false;$('codigo').value=''}};
$('codigo').onkeydown=e=>{if(e.key==='Enter')$('b2fa').click()};
$('todas').onclick=e=>{e.preventDefault();document.querySelectorAll('#lista input').forEach(i=>i.checked=true)};
$('nenhuma').onclick=e=>{e.preventDefault();document.querySelectorAll('#lista input').forEach(i=>i.checked=false)};
function render(j){$('json').textContent=JSON.stringify(j,null,2);$('tab').classList.remove('hidden');
  $('tab').querySelector('tbody').innerHTML=j.cidades.map(c=>{const k=c.calculo||{},nome=(document.querySelector(`#lista input[value="${c.bandeira_id}"]`)||{}).parentElement;
   const n=nome?nome.textContent.split('#')[0].trim():c.bandeira_id;const e=c.erro?`<span class="err">${c.erro.mensagem}</span>`:c.status;
   return `<tr><td>${n}</td><td>${e}${(c.avisos||[]).length?'<br><small>'+c.avisos.join('<br>')+'</small>':''}</td><td>${brl(k.ganhos_gerais)}</td><td>${k.cenario==='cenario_02'?(brl(k.taxa_central_fixo)+' × '+(k.corridas_finalizadas||0).toLocaleString('pt-BR')+' corr.<br><small>cenário 2</small>'):((k.taxa_central_pct??'—')+'%<br><small>cenário 1</small>')}</td><td>${brl(k.saldo_01)}</td><td>${brl(k.boleto_ubiz)}<br><small style="color:#667085">${brl(k.boleto_machine)}</small></td><td>${brl(k.seguro_app)}</td><td>${brl(k.base_royalties)}</td><td>${brl(k.royalty)}</td><td><b>${brl(k.valor_final)}</b></td></tr>`}).join('');
  msg($('m3'),`${j.cenario==='cenario_02'?'Cenário 2':'Cenário 1'} · ${j.status} · ${j.progresso.concluidas}/${j.progresso.total} cidades · erros ${j.progresso.com_erro} · total royalties ${brl(j.totais.valor_final)}`,j.status!=='erro')}
$('cenario').onchange=()=>{$('campoSeg').style.visibility=$('cenario').value==='cenario_02'?'hidden':'visible';
  $('bRodar').textContent=$('cenario').value?('Rodar '+($('cenario').value==='cenario_02'?'cenário 2':'cenário 1')+' (simulação, não grava)'):'Rodar (simulação, não grava)'};
$('bRodar').onclick=async()=>{const ids=[...document.querySelectorAll('#lista input:checked')].map(i=>i.value);
  const cenario=$('cenario').value;
  if(!cenario)return msg($('m3'),'Escolha o cenário da rodada.');
  if(!ids.length)return msg($('m3'),'Marque ao menos uma cidade.');$('bRodar').disabled=true;
  const cfg={taxa_valor:+$('pct').value,taxa_tipo:'percentual',taxa_seguro_app:+$('seg').value,taxa_seguro_app_tipo:'fixo',tarifa_boleto:+$('tar').value,tarifa_boleto_tipo:'fixo'};
  try{let j=await api('POST','/financeiro/royalties/rodar',{cenario,ano_mes:$('mes').value,bandeira_ids:ids,dry_run:true,config_padrao:cfg});render(j);
    while(j.status==='processing'){await new Promise(r=>setTimeout(r,3000));j=await api('GET','/financeiro/royalties/status/'+j.job_id);render(j)}}
  catch(e){msg($('m3'),e.message)}finally{$('bRodar').disabled=false}};
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # sem logar corpo/headers (credenciais)
        sys.stderr.write("%s %s\n" % (self.command, self.path.split("?")[0]))

    def _enviar(self, status, corpo, tipo="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", tipo + "; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._enviar(200, PAGINA.replace("__VPS__", VPS_PADRAO).encode(), "text/html")
        if self.path.startswith("/proxy/financeiro/"):
            return self._proxy()
        self._enviar(404, b'{"detail":"not found"}')

    def do_POST(self):
        if self.path.startswith("/proxy/financeiro/"):
            return self._proxy()
        self._enviar(404, b'{"detail":"not found"}')

    def _proxy(self):
        vps = (self.headers.get("X-Vps") or VPS_PADRAO).rstrip("/")
        if not vps.startswith("https://"):
            return self._enviar(400, b'{"detail":{"mensagem":"URL da VPS deve comecar com https://"}}')
        tamanho = int(self.headers.get("Content-Length") or 0)
        corpo = self.rfile.read(tamanho) if tamanho else None
        headers = {"Content-Type": "application/json", "X-API-Key": self.headers.get("X-API-Key", "")}
        if self.headers.get("X-Session-Token"):
            headers["X-Session-Token"] = self.headers["X-Session-Token"]
        req = urllib.request.Request(vps + self.path[len("/proxy"):], data=corpo, headers=headers, method=self.command)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return self._enviar(r.status, r.read())
        except urllib.error.HTTPError as e:
            return self._enviar(e.code, e.read() or b"{}")
        except Exception as e:
            return self._enviar(502, json.dumps({"detail": {"mensagem": f"VPS inacessível: {e}"}}).encode())


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORTA), Handler)
    url = f"http://127.0.0.1:{PORTA}"
    print(f"Console de royalties em {url}  (Ctrl+C para sair)")
    webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
