"""
Cobrança de royalties (financeiro Ubiz) via HTTP no painel TaxiMachine, sem navegador.

Fluxo (o mesmo do vídeo "COBRANÇA ROYALTIES - CENÁRIO 1"):
  1. Login da matriz (POST /site/login). O 2FA é digitado pelo dono, em 2 etapas:
     iniciar_login() → {login_id}; concluir_login_2fa(login_id, codigo) → session_token.
  2. Para cada cidade: POST /site/trocarCentral {bandeiraId}
  3. Ganhos gerais do mês fechado: GET /site/estatistica (tipo=operacao)
  4. Taxa da central / App / Seguro: GET /bandeira/taxas (antiga /bandeira/creditos)
  5. "Boleto da Machine": POST /demonstrativoMensal/index (bandeira + competência)
  6. calcular_cenario_01(): mesma fórmula da edge function `webhook-cenario-01`
     do machine-finance-hub, com o boleto real do demonstrativo.
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
import threading
import time
import uuid
from calendar import monthrange
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

from machine_notificacao_http import (
    BASE_URL,
    _parse_json_response,
    novo_http_painel,
    registrar_sessao,
)

log = logging.getLogger(__name__)

TZ_SP = ZoneInfo("America/Sao_Paulo")
LOGIN_PENDENTE_TTL_SEG = 5 * 60
LOGIN_PENDENTE_MAX_TENTATIVAS = 5
TARIFA_BOLETO_SEGURO_APP = 3.50  # igual ao webhook-seguro-app do finance-hub

_HEADERS_PAGINA = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "X-Requested-With": "",
}


class ErroFinanceiro(RuntimeError):
    """Erro com `codigo` estável para o front tratar."""

    def __init__(self, codigo: str, mensagem: str, status: int = 400):
        super().__init__(mensagem)
        self.codigo = codigo
        self.status = status


# ───────────────────────── números / datas ─────────────────────────


def parse_numero_br(valor: Any) -> float:
    """'R$ 238.840,54' → 238840.54 · '15%' → 15.0 · 8238 → 8238.0 · '---' → 0.0"""
    if valor is None:
        return 0.0
    if isinstance(valor, (int, float)):
        return float(valor)
    s = str(valor).replace("R$", "").replace("%", "").replace("\xa0", " ").strip()
    s = re.sub(r"[^\d,.\-]", "", s)
    if not s or s in {"-", "---"}:
        return 0.0
    last_c, last_d = s.rfind(","), s.rfind(".")
    if last_c != -1 and last_c > last_d:
        s = s.replace(".", "").replace(",", ".")
    elif last_d != -1 and last_c != -1:
        s = s.replace(",", "")
    elif last_d != -1 and s.count(".") > 1:
        s = s.replace(".", "")
    elif last_d != -1 and len(s) - last_d - 1 == 3 and "," not in s:
        # "238.840" (milhar sem decimais) — o painel sempre manda 2 casas, mas por segurança
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def validar_ano_mes(ano_mes: str) -> Tuple[int, int]:
    m = re.fullmatch(r"(\d{4})-(\d{2})", (ano_mes or "").strip())
    if not m:
        raise ErroFinanceiro("ano_mes_invalido", "Use ano_mes no formato AAAA-MM (ex.: 2026-10).")
    ano, mes = int(m.group(1)), int(m.group(2))
    if not 1 <= mes <= 12:
        raise ErroFinanceiro("ano_mes_invalido", "Mês inválido em ano_mes.")
    return ano, mes


def mes_passado_sp(hoje: Optional[date] = None) -> str:
    hoje = hoje or datetime.now(TZ_SP).date()
    ano, mes = (hoje.year, hoje.month - 1) if hoje.month > 1 else (hoje.year - 1, 12)
    return f"{ano:04d}-{mes:02d}"


MESES_PT = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]


def periodo_rotulo(ano_mes: str) -> str:
    ano, mes = validar_ano_mes(ano_mes)
    return f"Referente a {MESES_PT[mes - 1]} de {ano}"


# ───────────────────────── HTML helpers ─────────────────────────


def _texto(fragmento: str) -> str:
    sem_tags = re.sub(r"<[^>]+>", " ", fragmento or "")
    return re.sub(r"\s+", " ", html_lib.unescape(sem_tags)).strip()


def _texto_por_classe(bloco: str, classe: str) -> str:
    m = re.search(
        r"<(\w+)[^>]*class=\"[^\"]*\b" + re.escape(classe) + r"\b[^\"]*\"[^>]*>(.*?)</\1>",
        bloco,
        re.S | re.I,
    )
    return _texto(m.group(2)) if m else ""


def _sessao_expirada(resp: requests.Response) -> bool:
    url = (resp.url or "").lower()
    corpo = (resp.text or "")[:20000]
    return (
        url.rstrip("/").endswith("/site/login")
        or "/login" in url and "LoginForm" in corpo
        or ("LoginForm[username]" in corpo and "sso-centrais-iniciais" not in corpo)
    )


def _get_pagina(http: requests.Session, caminho: str, **kw) -> requests.Response:
    r = http.get(BASE_URL + caminho, headers={**_HEADERS_PAGINA, "Referer": BASE_URL + "/"}, timeout=60, **kw)
    if _sessao_expirada(r):
        raise ErroFinanceiro("sessao_expirada", "Sessão da Machine expirou. Faça login novamente.", 401)
    return r


# ───────────────────────── login em 2 etapas ─────────────────────────

_pendentes: Dict[str, Dict[str, Any]] = {}
_pendentes_lock = threading.Lock()


def _limpar_pendentes() -> None:
    corte = time.time() - LOGIN_PENDENTE_TTL_SEG
    for k in [k for k, v in _pendentes.items() if v["criado_em"] < corte]:
        _pendentes.pop(k, None)


def _finalizar_login(email: str, http: requests.Session) -> Dict[str, Any]:
    token = registrar_sessao(email, http)
    try:
        centrais = listar_centrais(http)
    except Exception as exc:  # não impede o login
        log.warning("Login financeiro OK, mas falhou ao listar centrais: %s", exc)
        centrais = []
    return {
        "sucesso": True,
        "status": "autenticado",
        "email": email,
        "session_token": token,
        "centrais": centrais,
    }


def iniciar_login(email: str, senha: str) -> Dict[str, Any]:
    """
    POST /site/login. Se a Machine pedir 2FA, guarda a sessão pendente e devolve
    {status:"aguardando_2fa", login_id}. O front mostra a tela "digite o código".
    """
    http = novo_http_painel()
    http.get(BASE_URL + "/", timeout=30)
    r = http.post(
        BASE_URL + "/site/login",
        data={
            "LoginForm[username]": email,
            "LoginForm[password]": senha,
            "LoginForm[rememberMe]": "0",
        },
        headers={"Referer": BASE_URL + "/"},
        timeout=30,
    )
    data = _parse_json_response(r)

    if data.get("cadastrar2FA"):
        raise ErroFinanceiro(
            "2fa_nao_cadastrado",
            "A conta ainda não tem 2FA cadastrado. Conclua o cadastro no painel da Machine e tente de novo.",
        )
    if data.get("solicitarCodigo2FA"):
        login_id = str(uuid.uuid4())
        with _pendentes_lock:
            _limpar_pendentes()
            _pendentes[login_id] = {"email": email, "http": http, "criado_em": time.time(), "tentativas": 0}
        return {
            "sucesso": True,
            "status": "aguardando_2fa",
            "login_id": login_id,
            "expira_em_seg": LOGIN_PENDENTE_TTL_SEG,
            "mensagem": "Digite o código de 6 dígitos do autenticador do responsável pela conta.",
        }
    if not data.get("success"):
        raise ErroFinanceiro("credenciais_invalidas", "E-mail ou senha da Machine inválidos.", 401)
    return _finalizar_login(email, http)


def concluir_login_2fa(login_id: str, codigo: str) -> Dict[str, Any]:
    codigo = re.sub(r"\D", "", codigo or "")
    if len(codigo) != 6:
        raise ErroFinanceiro("codigo_invalido", "O código deve ter 6 dígitos.")
    with _pendentes_lock:
        _limpar_pendentes()
        pend = _pendentes.get(login_id)
    if not pend:
        raise ErroFinanceiro("login_expirado", "Login expirado. Comece de novo e digite o código em até 5 minutos.", 410)

    r = pend["http"].post(
        BASE_URL + "/site/verificar2FA",
        data={"code": codigo},
        headers={"Referer": BASE_URL + "/"},
        timeout=30,
    )
    data = _parse_json_response(r)
    if not data.get("success"):
        with _pendentes_lock:
            pend["tentativas"] += 1
            restantes = LOGIN_PENDENTE_MAX_TENTATIVAS - pend["tentativas"]
            if restantes <= 0:
                _pendentes.pop(login_id, None)
        if restantes <= 0:
            raise ErroFinanceiro("login_expirado", "Muitas tentativas. Comece o login de novo.", 410)
        raise ErroFinanceiro(
            "codigo_invalido",
            f"{data.get('message') or 'Código incorreto.'} Tentativas restantes: {restantes}.",
        )

    with _pendentes_lock:
        _pendentes.pop(login_id, None)
    return _finalizar_login(pend["email"], pend["http"])


# ───────────────────────── centrais (seletor de cidade) ─────────────────────────


def parse_centrais(html: str) -> List[Dict[str, Any]]:
    m = re.search(
        r"<script[^>]*id=\"sso-centrais-iniciais\"[^>]*>(.*?)</script>", html or "", re.S | re.I
    )
    if not m:
        return []
    try:
        itens = json.loads(m.group(1).strip())
    except json.JSONDecodeError:
        return []
    return [
        {
            "bandeira_id": str(i.get("bandeira_id")),
            "nome": (i.get("bandeira_nome") or "").strip(),
            "cidade": (i.get("cidade_nome") or "").strip(),
            "atual": bool(i.get("ehAtual")),
        }
        for i in itens
        if i.get("bandeira_id")
    ]


def listar_centrais(http: requests.Session) -> List[Dict[str, Any]]:
    r = _get_pagina(http, "/demonstrativoMensal/index")
    centrais = parse_centrais(r.text)
    if not centrais:
        raise ErroFinanceiro(
            "centrais_nao_encontradas",
            "Não encontrei a lista de cidades. A conta precisa ser da matriz (com seletor de centrais).",
        )
    return centrais


def trocar_central(http: requests.Session, bandeira_id: str, confirmar: bool = True) -> None:
    """
    Equivale a escolher a cidade no seletor do topo do painel.
    Não segue o redirect (que carregaria a home inteira). Com confirmar=False, quem chama
    confirma depois com `central_ativa(html)` numa página que já vai baixar (ex.: taxas).
    """
    bandeira_id = str(bandeira_id)
    r = http.post(
        BASE_URL + "/site/trocarCentral",
        data={"bandeiraId": bandeira_id},
        headers={**_HEADERS_PAGINA, "Referer": BASE_URL + "/demonstrativoMensal/index"},
        timeout=60,
        allow_redirects=False,
    )
    if r.status_code >= 400:
        raise ErroFinanceiro("troca_central_falhou", f"Machine recusou a troca para a central {bandeira_id} (HTTP {r.status_code}).")
    if "site/login" in (r.headers.get("Location") or ""):
        raise ErroFinanceiro("sessao_expirada", "Sessão da Machine expirou. Faça login novamente.", 401)
    if confirmar:
        confirmar_central(listar_centrais_html(http), bandeira_id)


def central_ativa(html: str) -> Optional[str]:
    return next((c["bandeira_id"] for c in parse_centrais(html) if c["atual"]), None)


def confirmar_central(html: str, bandeira_id: str) -> None:
    atual = central_ativa(html)
    if atual is not None and atual != str(bandeira_id):
        raise ErroFinanceiro(
            "troca_central_falhou",
            f"Não foi possível trocar para a central {bandeira_id} (atual: {atual}).",
        )


def listar_centrais_html(http: requests.Session) -> str:
    return _get_pagina(http, "/demonstrativoMensal/index").text


# ───────────────────────── ganhos gerais (Painel → Indicadores) ─────────────────────────


def _qtd_fatia(grafico: List[Dict[str, Any]], *chaves: str) -> float:
    for f in grafico or []:
        rotulo = f"{f.get('chave') or ''} {f.get('texto') or ''}".lower()
        if any(c in rotulo for c in chaves):
            return parse_numero_br(f.get("quantidade") if f.get("quantidade") not in (None, "") else f.get("valor"))
    return 0.0


def parse_indicadores(payload: Dict[str, Any]) -> Dict[str, Any]:
    ganho = payload.get("ganhoGeral") or {}
    solicitada = payload.get("solicitada") or {}
    origem = payload.get("origemFinalizada") or {}
    graf_sol = solicitada.get("grafico") or []
    return {
        "ganhos_gerais": round(parse_numero_br(ganho.get("valor")), 2),
        "ganhos_por_pagamento": {
            (f.get("texto") or "").strip(): parse_numero_br(f.get("quantidade") or f.get("valor"))
            for f in ganho.get("grafico") or []
        },
        "corridas_solicitadas": int(parse_numero_br(solicitada.get("valor"))),
        "corridas_finalizadas": int(parse_numero_br(origem.get("valor")) or _qtd_fatia(graf_sol, "finaliz")),
        "corridas_canceladas": int(_qtd_fatia(graf_sol, "cancel")),
        "corridas_nao_atendidas": int(_qtd_fatia(graf_sol, "atendid")),
    }


def obter_ganhos_mes(
    http: requests.Session, ano_mes: str, bandeira_id: Optional[str] = None, hoje: Optional[date] = None
) -> Dict[str, Any]:
    ano, mes = validar_ano_mes(ano_mes)
    avisos: List[str] = []
    if ano_mes == mes_passado_sp(hoje):
        params = {"periodo": "mes_passado", "periodo_data_inicial": "", "periodo_data_final": ""}
    else:
        ultimo = monthrange(ano, mes)[1]
        params = {
            "periodo": "personalizado",
            "periodo_data_inicial": f"01/{mes:02d}/{ano}",
            "periodo_data_final": f"{ultimo:02d}/{mes:02d}/{ano}",
        }
        avisos.append("Período personalizado: confira os ganhos com a tela do painel.")
    params["tipo"] = "operacao"
    if bandeira_id:
        params["bandeira_id"] = str(bandeira_id)

    r = http.get(
        BASE_URL + "/site/estatistica",
        params=params,
        headers={"X-Requested-With": "XMLHttpRequest", "Referer": BASE_URL + "/"},
        timeout=60,
    )
    if r.status_code == 403:
        raise ErroFinanceiro("sem_permissao_painel", "Conta sem permissão para Indicadores do Painel.", 403)
    if _sessao_expirada(r):
        raise ErroFinanceiro("sessao_expirada", "Sessão da Machine expirou. Faça login novamente.", 401)
    payload = _parse_json_response(r)
    out = parse_indicadores(payload if isinstance(payload, dict) else {})
    out["periodo_machine"] = params["periodo"]
    out["avisos"] = avisos
    return out


# ───────────────────────── Consultar corridas (filtro do mês, status Finalizada) ─────────────────────────

FILTRO_CORRIDAS_TIMEOUT_SEG = 240


def _intervalos_polling():
    """1 s nos primeiros 10 s, depois 2 s, depois 3 s (a Machine leva de segundos a ~1 min)."""
    for _ in range(10):
        yield 1.0
    for _ in range(10):
        yield 2.0
    while True:
        yield 3.0


def iniciar_filtro_corridas(http: requests.Session, ano_mes: str, resetar: bool = False) -> Dict[str, Any]:
    """Dispara o filtro (dia 1 00:00 ao último dia 23:59, status Finalizada) na central ativa."""
    from machine_relatorio_http import _form_corridas, _headers_corridas

    ano, mes = validar_ano_mes(ano_mes)
    inicio, fim = date(ano, mes, 1), date(ano, mes, monthrange(ano, mes)[1])
    form = _form_corridas(inicio, fim)
    form["HistoricoFilterForm[status_solicitacao]"] = "F"
    if resetar:
        http.get(BASE_URL + "/solicitacao/historicoCorridas2", params={"resetSesion": "1"},
                 headers={**_HEADERS_PAGINA, "Referer": BASE_URL + "/"}, timeout=60)
    r = http.post(BASE_URL + "/solicitacao/relatorioCorridas", data=form, headers=_headers_corridas(), timeout=60)
    if _sessao_expirada(r):
        raise ErroFinanceiro("sessao_expirada", "Sessão da Machine expirou. Faça login novamente.", 401)
    data = _parse_json_response(r)
    report_id = data.get("report_id")
    if not data.get("success") or not report_id:
        raise ErroFinanceiro("filtro_corridas_falhou", data.get("message") or "A Machine recusou o filtro de corridas.")
    return {"report_id": report_id, "sincrono": data.get("tipo") == "sincrono", "inicio": inicio, "fim": fim,
            "ano_mes": ano_mes, "resetado": resetar}


def concluir_filtro_corridas(http: requests.Session, filtro: Dict[str, Any]) -> Dict[str, Any]:
    from machine_dashboard_http import _parse_grid_render
    from machine_relatorio_http import _headers_corridas, status_corridas

    if not filtro["sincrono"]:
        limite = time.time() + FILTRO_CORRIDAS_TIMEOUT_SEG
        for espera in _intervalos_polling():
            st = status_corridas(http, filtro["report_id"], None)
            if st["cancelado"]:
                raise ErroFinanceiro("filtro_corridas_cancelado", "O filtro de corridas foi cancelado pela Machine.")
            if st["filtro_pronto"]:
                break
            if time.time() > limite:
                raise ErroFinanceiro("filtro_corridas_timeout", "Tempo esgotado esperando o filtro de corridas.")
            time.sleep(espera)

    r = http.post(BASE_URL + "/solicitacao/historicoCorridas2", data={"json": "true"},
                  headers=_headers_corridas(), timeout=60)
    payload = _parse_json_response(r)
    render = payload.get("render") or ""
    texto_filtro = _texto(render[:4000])
    inicio, fim = filtro["inicio"], filtro["fim"]
    # Sem o GET de reset, confere se o grid é mesmo do período pedido; se não, refaz com reset.
    if f"{inicio:%d/%m/%Y}" not in texto_filtro and not filtro.get("resetado"):
        return concluir_filtro_corridas(http, iniciar_filtro_corridas(http, filtro["ano_mes"], resetar=True))
    _, meta = _parse_grid_render(render)
    if meta.get("total") is None:
        if re.search(r"nenhum resultado", render, re.I):
            meta["total"] = 0
        else:
            raise ErroFinanceiro("filtro_corridas_sem_total", "Não encontrei o total de corridas no resultado do filtro.")
    return {
        "total": int(meta["total"]),
        "periodo": f"{inicio:%d/%m/%Y} 00:00 a {fim:%d/%m/%Y} 23:59",
        "status_confirmado": "Finalizada" in texto_filtro,
        "resumo": meta.get("summary"),
    }


def contar_corridas_finalizadas(http: requests.Session, ano_mes: str) -> Dict[str, Any]:
    """
    Igual ao financeiro: Corridas › Consultar corridas › dia 1 00:00 até o último dia 23:59,
    status Finalizada → "1-30 de N resultados". Usa a central ATIVA (fazer trocar_central antes).
    O Painel pode dar alguns a menos (Passos 10/2026: Painel 5.096, filtro 5.098).
    """
    return concluir_filtro_corridas(http, iniciar_filtro_corridas(http, ano_mes))


# ───────────────────────── taxas (/bandeira/taxas) ─────────────────────────

_TITULO_TAXA = {
    "taxa da central": "taxa_central",
    "taxa app": "taxa_app",
    "taxa seguro app": "taxa_seguro_app",
}


def _parse_taxa_visivel(texto: str) -> Tuple[Optional[float], Optional[float]]:
    """'15% - R$5,00' → (15.0, 5.0) · 'R$0,20' → (None, 0.2) · '15%' → (15.0, None)"""
    t = (texto or "").replace("\n", " ")
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*%\s*[-–]\s*R\$\s*([\d.,]+)", t)
    if m:
        return parse_numero_br(m.group(1)), parse_numero_br(m.group(2))
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*%", t)
    if m:
        return parse_numero_br(m.group(1)), None
    m = re.search(r"R\$\s*([\d.,]+)", t)
    if m:
        return None, parse_numero_br(m.group(1))
    return None, None


def parse_taxas(html: str) -> Dict[str, Any]:
    taxas: Dict[str, Any] = {}
    blocos = re.findall(
        r"<button[^>]*class=\"[^\"]*\bbtn-tax\b[^\"]*\"[^>]*>(.*?)</button>", html or "", re.S | re.I
    )
    for bloco in blocos:
        titulo = _texto_por_classe(bloco, "short_title")
        chave = _TITULO_TAXA.get(titulo.lower())
        if not chave or chave in taxas:
            continue
        texto_taxa = _texto_por_classe(bloco, "taxa-content")
        pct, fixo = _parse_taxa_visivel(texto_taxa)
        cats = _texto_por_classe(bloco, "categorias-content")
        taxas[chave] = {
            "titulo": titulo,
            "texto": texto_taxa,
            "percentual": pct,
            "valor_fixo": fixo,
            "categorias": [c.strip() for c in re.split(r",\s*", cats) if c.strip()],
            "tipos_pagamento": _texto_por_classe(bloco, "tipos-pagamento-content"),
        }
    if "taxa_central" not in taxas:
        # layout antigo: inputs do 1º formulário
        pct = re.search(r"name=\"TaxaBandeira\[valor_taxa_central_fx1\]\"[^>]*value=\"([^\"]*)\"", html or "")
        fixo = re.search(r"name=\"TaxaBandeira\[valor_taxa_central_fx3\]\"[^>]*value=\"([^\"]*)\"", html or "")
        if pct:
            taxas["taxa_central"] = {
                "titulo": "Taxa da central",
                "texto": "",
                "percentual": parse_numero_br(pct.group(1)),
                "valor_fixo": parse_numero_br(fixo.group(1)) if fixo else None,
                "categorias": [],
                "tipos_pagamento": "",
            }
    return taxas


def obter_taxas_bandeira(http: requests.Session) -> Dict[str, Any]:
    ultimo_erro = None
    for caminho in ("/bandeira/taxas", "/bandeira/creditos"):
        r = _get_pagina(http, caminho)
        if r.status_code == 403:
            raise ErroFinanceiro("sem_permissao_taxas", "Conta sem permissão para a tela de taxas.", 403)
        if r.status_code >= 400:
            ultimo_erro = f"HTTP {r.status_code} em {caminho}"
            continue
        taxas = parse_taxas(r.text)
        if taxas.get("taxa_central"):
            taxas["_origem"] = caminho
            taxas["_central_ativa"] = central_ativa(r.text)
            return taxas
        ultimo_erro = f"Taxa da central não encontrada em {caminho}"
    raise ErroFinanceiro("taxas_nao_encontradas", ultimo_erro or "Taxas não encontradas.")


# ───────────────────────── boleto (/demonstrativoMensal) ─────────────────────────


def _inputs_ocultos(form_html: str) -> List[Tuple[str, str]]:
    pares = []
    for tag in re.findall(r"<input[^>]*type=\"hidden\"[^>]*>", form_html or "", re.I):
        n = re.search(r"name=\"([^\"]+)\"", tag)
        v = re.search(r"value=\"([^\"]*)\"", tag)
        if n:
            pares.append((html_lib.unescape(n.group(1)), html_lib.unescape(v.group(1) if v else "")))
    return pares


def _option_selecionada(html: str, select_id: str) -> Optional[str]:
    m = re.search(r"<select[^>]*id=\"" + re.escape(select_id) + r"\"[^>]*>(.*?)</select>", html or "", re.S)
    if not m:
        return None
    sel = re.search(r"<option[^>]*value=\"([^\"]*)\"[^>]*selected", m.group(1))
    return sel.group(1) if sel else None


def parse_demonstrativo(html: str) -> Dict[str, Any]:
    m = re.search(r"<table[^>]*class=\"[^\"]*\bitems\b[^\"]*\"[^>]*>(.*?)</table>", html or "", re.S | re.I)
    itens: List[Dict[str, Any]] = []
    total_tabela = None
    if m:
        corpo = re.search(r"<tbody[^>]*>(.*?)</tbody>", m.group(1), re.S | re.I)
        for linha in re.findall(r"<tr[^>]*>(.*?)</tr>", corpo.group(1) if corpo else "", re.S | re.I):
            cols = [_texto(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", linha, re.S | re.I)]
            if len(cols) < 4:
                continue
            desc = cols[0]
            if desc.lower().startswith("total a pagar"):
                total_tabela = parse_numero_br(cols[3])
                continue
            itens.append(
                {
                    "descricao": desc,
                    "quantidade": None if cols[1] in ("---", "") else parse_numero_br(cols[1]),
                    "valor_unitario": None if cols[2] in ("---", "") else parse_numero_br(cols[2]),
                    "debito": parse_numero_br(cols[3]),
                    "credito": parse_numero_br(cols[4]) if len(cols) > 4 else 0.0,
                    "subtotal": cols[1] in ("---", "") and cols[2] in ("---", ""),
                }
            )
    vp = re.search(r"class=\"valor_total\"[^>]*>\s*Valor a pagar:\s*([^<]+)<", html or "", re.I)
    valor_a_pagar = parse_numero_br(vp.group(1)) if vp else total_tabela
    return {
        "itens": itens,
        "total_a_pagar": total_tabela,
        "valor_a_pagar": valor_a_pagar,
        "bandeira_id": _option_selecionada(html, "FinCobrancaBandeira_bandeira_id"),
        "ano_mes": _option_selecionada(html, "data_ano_mes"),
    }


def _diagnostico_html(r: requests.Response) -> Dict[str, Any]:
    html = r.text or ""
    titulo = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    return {
        "http": r.status_code,
        "url": (r.url or "").replace(BASE_URL, ""),
        "titulo": _texto(titulo.group(1)) if titulo else "",
        "tamanho": len(html),
        "tem_tabela_items": bool(re.search(r"class=\"[^\"]*\bitems\b", html)),
        "tem_valor_total": "valor_total" in html,
        "tem_seletor_bandeira": "FinCobrancaBandeira_bandeira_id" in html,
        "tem_data_ano_mes": "data_ano_mes" in html,
        "trecho": _texto(html[html.find("<body"):])[:400] if "<body" in html else _texto(html)[:400],
    }


def campos_demonstrativo(http: requests.Session) -> Dict[str, Any]:
    """Uma GET por rodada: campos ocultos do formulário + lista de centrais (mesma página)."""
    r = _get_pagina(http, "/demonstrativoMensal/index")
    form = re.search(r"<form[^>]*action=\"[^\"]*demonstrativoMensal/index\"[^>]*>(.*?)</form>", r.text, re.S)
    return {"ocultos": _inputs_ocultos(form.group(1) if form else ""), "centrais": parse_centrais(r.text), "_resp": r}


def obter_demonstrativo(
    http: requests.Session, bandeira_id: str, ano_mes: str, campos: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    "Boleto da Machine". Funciona com a sessão na central da MATRIZ (seletor de bandeiras).
    Tenta POST do formulário (como o painel) e, se não vier tabela, GET com os mesmos parâmetros.
    """
    validar_ano_mes(ano_mes)
    campos = campos or campos_demonstrativo(http)
    r_get = campos["_resp"]
    dados = list(campos["ocultos"])
    dados += [("FinCobrancaBandeira[bandeira_id]", str(bandeira_id)), ("data_ano_mes", ano_mes)]
    tentativas = []
    r = http.post(
        BASE_URL + "/demonstrativoMensal/index",
        data=dados,
        headers={**_HEADERS_PAGINA, "Referer": BASE_URL + "/demonstrativoMensal/index"},
        timeout=60,
    )
    tentativas.append(("post", r))
    dem = parse_demonstrativo(r.text)
    if dem["valor_a_pagar"] is None:
        r2 = _get_pagina(http, "/demonstrativoMensal/index", params=dict(dados))
        tentativas.append(("get", r2))
        dem2 = parse_demonstrativo(r2.text)
        if dem2["valor_a_pagar"] is not None:
            dem = dem2
    if _sessao_expirada(tentativas[-1][1]):
        raise ErroFinanceiro("sessao_expirada", "Sessão da Machine expirou. Faça login novamente.", 401)
    if dem["valor_a_pagar"] is None:
        diag = {m: _diagnostico_html(resp) for m, resp in tentativas}
        diag["get_inicial"] = _diagnostico_html(r_get)
        log.warning("demonstrativo vazio bandeira=%s %s diag=%s", bandeira_id, ano_mes, diag)
        erro = ErroFinanceiro("demonstrativo_vazio", f"Demonstrativo sem total para {bandeira_id} em {ano_mes}.")
        erro.diagnostico = diag  # type: ignore[attr-defined]
        raise erro
    if dem["bandeira_id"] and dem["bandeira_id"] != str(bandeira_id):
        raise ErroFinanceiro(
            "demonstrativo_cidade_errada",
            f"Demonstrativo voltou da bandeira {dem['bandeira_id']}, esperado {bandeira_id}.",
        )
    if dem["ano_mes"] and dem["ano_mes"] != ano_mes:
        raise ErroFinanceiro(
            "demonstrativo_sem_competencia",
            f"Competência {ano_mes} indisponível no demonstrativo (veio {dem['ano_mes']}).",
        )
    return dem


# ───────────────────────── cálculo (cenário 01) ─────────────────────────

CENARIOS = ("cenario_01", "cenario_02", "cenario_03", "cenario_04")
NOMES_CENARIO = {
    "cenario_01": "cenário 1 (% sobre o transacionado)",
    "cenario_02": "cenário 2 (R$ fixo por corrida)",
    "cenario_03": "cenário 3 (entradas − saídas do extrato)",
    "cenario_04": "cenário 4 (taxa da central + taxa do cartão)",
}


def _aplicar(base: float, valor: float, tipo: str) -> float:
    return base * (valor / 100.0) if (tipo or "percentual") == "percentual" else valor


def calcular_seguro_app(corridas_finalizadas: float, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Igual ao webhook-seguro-app: taxa da cidade (se > 0) senão a global; + R$3,50 de boleto."""
    taxa_cidade = float(cfg.get("taxa_seguro_app") or 0)
    usa_cidade = taxa_cidade > 0
    taxa = taxa_cidade if usa_cidade else float(cfg.get("taxa_seguro_app_global") or 0)
    tipo = (cfg.get("taxa_seguro_app_tipo") if usa_cidade else cfg.get("taxa_seguro_app_global_tipo")) or "percentual"
    if taxa <= 0:
        return {"valor": 0.0, "taxa": 0.0, "tipo": tipo, "origem": "nao_configurada"}
    valor = corridas_finalizadas * (taxa / 100.0) if tipo == "percentual" else corridas_finalizadas * taxa
    valor = round(valor + TARIFA_BOLETO_SEGURO_APP, 2)
    return {"valor": valor, "taxa": taxa, "tipo": tipo, "origem": "cidade" if usa_cidade else "global"}


# Tabela de preços que a Ubiz cobra das franquias (não é o preço da Machine).
# Fonte: financeiro Ubiz (05/11/2026). Pode ser sobrescrita por cidade/empresa (cfg["tabela_ubiz"]).
TABELA_UBIZ_PADRAO: Dict[str, float] = {
    "corridas_finalizadas": 0.33,
    "corridas_nao_finalizadas": 0.17,  # perdidas e canceladas
    "sms": 0.14,
    "validacao_cpf": 0.14,
    "rota_estimativa": 0.02,
    "rota_viagem": 0.02,
    "transbordo": 0.25,
    "saque_pix": 0.50,
    "tarifa_boleto": 3.50,
}


def classificar_item_demonstrativo(descricao: str) -> Optional[str]:
    d = (descricao or "").lower()
    if "não finalizada" in d or "nao finalizada" in d or "perdida" in d or "cancelad" in d:
        return "corridas_nao_finalizadas"
    if "finalizada" in d:
        return "corridas_finalizadas"
    if "estimativa" in d:
        return "rota_estimativa"
    if "rota" in d and ("viagem" in d or "corrida" in d):
        return "rota_viagem"
    if "cpf" in d:
        return "validacao_cpf"
    if "sms" in d:
        return "sms"
    if "transbordo" in d:
        return "transbordo"
    if "saque" in d or "pix" in d:
        return "saque_pix"
    return None


def calcular_boleto_ubiz(itens_demonstrativo: List[Dict[str, Any]], tabela: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """
    Boleto que a Ubiz considera: QUANTIDADES do Demonstrativo Mensal da Machine × tabela Ubiz
    + tarifa de boleto. (Conferido com o cálculo manual de Alfenas 10/2026: R$ 3.739,09.)
    """
    tab = {**TABELA_UBIZ_PADRAO, **{k: float(v) for k, v in (tabela or {}).items() if v is not None}}
    linhas, avisos, total = [], [], 0.0
    for it in itens_demonstrativo or []:
        if it.get("subtotal") or it.get("quantidade") in (None, ""):
            continue
        chave = classificar_item_demonstrativo(it.get("descricao", ""))
        qtd = float(it["quantidade"])
        if chave is None:
            valor = float(it.get("debito") or 0)
            avisos.append(f"Item do demonstrativo sem preço na tabela Ubiz: '{it.get('descricao')}'. Usado o valor da Machine (R$ {valor:.2f}).")
            linhas.append({"descricao": it.get("descricao"), "chave": None, "quantidade": qtd, "tarifa": None, "valor": round(valor, 2)})
        else:
            valor = qtd * tab[chave]
            linhas.append({"descricao": it.get("descricao"), "chave": chave, "quantidade": qtd, "tarifa": tab[chave], "valor": round(valor, 2)})
        total += valor
    tarifa = tab["tarifa_boleto"]
    return {"itens": linhas, "subtotal": round(total, 2), "tarifa_boleto": tarifa, "total": round(total + tarifa, 2), "avisos": avisos}


def calcular_cenario(entradas: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    cenario = entradas.get("cenario") or "cenario_01"
    if cenario == "cenario_03":
        return calcular_cenario_03(entradas, cfg)
    if cenario == "cenario_04":
        return calcular_cenario_04(entradas, cfg)
    return _calcular_cenario_01_02(entradas, cfg)


def _calcular_cenario_01_02(entradas: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    entradas: ganhos_gerais, taxa_central_pct, corridas_finalizadas,
              itens_demonstrativo (quantidades da Machine), boleto_machine (só referência)
    cfg: taxa_valor, taxa_tipo (royalty Ubiz da cidade), taxa_seguro_app[_tipo],
         taxa_seguro_app_global[_tipo], tarifa_boleto, tarifa_boleto_tipo, tabela_ubiz

    Regra do financeiro Ubiz:
      cenário 01 (taxa central em %):  base = ganhos × taxa_central% − boleto_ubiz − seguro_app
      cenário 02 (taxa central fixa):  base = corridas_finalizadas × R$ fixo − boleto_ubiz
      valor_final = base × % da cidade + tarifa de boleto
    """
    avisos: List[str] = []
    cenario = entradas.get("cenario") or "cenario_01"
    ganho = float(entradas["ganhos_gerais"])
    pct_central = entradas.get("taxa_central_pct")
    fixo_central = entradas.get("taxa_central_fixo")
    corridas = float(entradas.get("corridas_finalizadas") or 0)
    tabela = dict(cfg.get("tabela_ubiz") or {})
    if cfg.get("tarifa_boleto") is not None and "tarifa_boleto" not in tabela:
        tabela["tarifa_boleto"] = float(cfg["tarifa_boleto"])
    if entradas.get("itens_demonstrativo") is not None:
        boleto_ubiz = calcular_boleto_ubiz(entradas["itens_demonstrativo"], tabela)
        avisos.extend(boleto_ubiz["avisos"])
        boleto = boleto_ubiz["total"]
    else:  # compatibilidade: só o total informado
        boleto_ubiz = None
        boleto = float(entradas["boleto_machine"])

    if cenario == "cenario_02":
        # Cenário 2: taxa da central FIXA por corrida → corridas finalizadas × R$ fixo.
        saldo_01 = corridas * float(fixo_central)
    else:
        saldo_01 = ganho * float(pct_central) / 100.0

    descontar_seguro = cfg.get("descontar_seguro")
    if descontar_seguro is None:
        descontar_seguro = cenario == "cenario_01"  # no cenário 2 o financeiro não desconta seguro
    if descontar_seguro:
        seguro = calcular_seguro_app(corridas, cfg)
        if seguro["origem"] == "nao_configurada":
            avisos.append("Seguro app sem taxa configurada (cidade nem global): descontado R$ 0,00.")
    else:
        seguro = {"valor": 0.0, "taxa": 0.0, "tipo": "fixo", "origem": "nao_descontado_no_cenario"}
    total_deducoes = boleto + seguro["valor"]
    base = saldo_01 - total_deducoes
    if base < 0:
        avisos.append("Base negativa: descontos maiores que a taxa da central.")

    taxa_valor = float(cfg.get("taxa_valor") or 0)
    taxa_tipo = cfg.get("taxa_tipo") or "percentual"
    if taxa_valor <= 0:
        avisos.append("Cidade sem % de royalty configurada.")
    royalty = _aplicar(base, taxa_valor, taxa_tipo)
    tarifa_boleto = _aplicar(base, float(cfg.get("tarifa_boleto") or 0), cfg.get("tarifa_boleto_tipo") or "fixo")
    valor_final = royalty + tarifa_boleto

    r2 = lambda v: round(v, 2)  # noqa: E731
    return {
        "cenario": cenario,
        "ganhos_gerais": r2(ganho),
        "corridas_finalizadas": int(corridas),
        "taxa_central_pct": float(pct_central) if pct_central is not None else None,
        "taxa_central_fixo": float(fixo_central) if fixo_central is not None else None,
        "saldo_01": r2(saldo_01),
        "boleto_ubiz": r2(boleto),
        "boleto_ubiz_detalhe": boleto_ubiz,
        "boleto_machine": r2(float(entradas.get("boleto_machine") or 0)),
        "seguro_app": seguro["valor"],
        "seguro_app_detalhe": seguro,
        "total_deducoes": r2(total_deducoes),
        "base_royalties": r2(base),
        "taxa_cidade": taxa_valor,
        "taxa_cidade_tipo": taxa_tipo,
        "royalty": r2(royalty),
        "tarifa_boleto": r2(tarifa_boleto),
        "valor_final": r2(valor_final),
        "avisos": avisos,
    }


def coletar_cidade(
    http: requests.Session,
    bandeira_id: str,
    ano_mes: str,
    trocar: bool = True,
    demonstrativo: Optional[Dict[str, Any]] = None,
    cenario: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Busca na Machine tudo que o cálculo precisa, para uma cidade.
    `demonstrativo` deve ser lido ANTES da troca de central (a tela só funciona na matriz).
    Ordem pensada para tempo: troca → taxas (confirma central e cenário) → dispara o filtro de
    corridas → ganhos enquanto a Machine processa → espera o filtro.
    """
    tempos: Dict[str, float] = {}
    t = time.time()

    def marca(etapa: str) -> None:
        nonlocal t
        agora = time.time()
        tempos[etapa] = round(agora - t, 2)
        t = agora

    dem = demonstrativo if demonstrativo is not None else obter_demonstrativo(http, bandeira_id, ano_mes)
    marca("demonstrativo")
    if trocar:
        trocar_central(http, bandeira_id, confirmar=False)
        marca("troca_central")
    taxas = obter_taxas_bandeira(http)
    if trocar:
        ativa = taxas.get("_central_ativa")
        if ativa is None:  # página de taxas sem o seletor: confirma do jeito antigo
            confirmar_central(listar_centrais_html(http), bandeira_id)
        elif ativa != str(bandeira_id):
            raise ErroFinanceiro("troca_central_falhou", f"Não foi possível trocar para a central {bandeira_id} (atual: {ativa}).")
    marca("taxas")

    pct = taxas["taxa_central"].get("percentual")
    fixo = taxas["taxa_central"].get("valor_fixo")
    if pct is not None:
        cenario_machine = "cenario_01"  # % sobre o transacionado
    elif fixo:
        cenario_machine = "cenario_02"  # R$ fixo por corrida finalizada
    else:
        raise ErroFinanceiro("taxa_central_invalida", "Não consegui ler a Taxa da central (nem % nem R$).")
    if cenario in ("cenario_01", "cenario_02") and cenario != cenario_machine:
        texto = taxas["taxa_central"].get("texto") or ""
        raise ErroFinanceiro(
            "cidade_fora_do_cenario",
            f"Cidade não é do {NOMES_CENARIO[cenario]}: a Taxa da central na Machine é '{texto}' "
            f"({NOMES_CENARIO[cenario_machine]}). Rode esta cidade no {NOMES_CENARIO[cenario_machine]}.",
        )
    cenario = cenario or cenario_machine
    # Quantidade de corridas: mesmo método do financeiro (Consultar corridas, status Finalizada).
    # Dispara o filtro, busca os ganhos enquanto a Machine processa e só então espera.
    filtro_iniciado, erro_filtro = None, None
    try:
        filtro_iniciado = iniciar_filtro_corridas(http, ano_mes)
    except ErroFinanceiro as exc:
        if exc.codigo == "sessao_expirada":
            raise
        erro_filtro = exc
    ganhos = obter_ganhos_mes(http, ano_mes, bandeira_id=bandeira_id)
    marca("ganhos")
    try:
        if erro_filtro:
            raise erro_filtro
        filtro = concluir_filtro_corridas(http, filtro_iniciado)
        marca("filtro_corridas")
        corridas = filtro["total"]
        if not filtro["status_confirmado"]:
            ganhos["avisos"].append("Filtro de corridas sem confirmação do status Finalizada: confira a quantidade.")
        if corridas != ganhos["corridas_finalizadas"]:
            ganhos["avisos"].append(
                f"Consultar corridas: {corridas} finalizadas (Painel mostra {ganhos['corridas_finalizadas']}). Usado o filtro."
            )
    except ErroFinanceiro as exc:
        if exc.codigo == "sessao_expirada":
            raise
        filtro = {"erro": str(exc)}
        corridas = ganhos["corridas_finalizadas"]
        ganhos["avisos"].append(f"Filtro de Consultar corridas falhou ({exc}); usada a quantidade do Painel.")
    return {
        "ganhos": ganhos,
        "taxas": taxas,
        "demonstrativo": dem,
        "consultar_corridas": filtro,
        "tempos_s": tempos,
        "entradas_calculo": {
            "ganhos_gerais": ganhos["ganhos_gerais"],
            "cenario": cenario,
            "taxa_central_pct": pct,
            "taxa_central_fixo": fixo if pct is None else None,
            "boleto_machine": dem["valor_a_pagar"],
            "itens_demonstrativo": dem["itens"],
            "corridas_finalizadas": corridas,
        },
    }


# ═════════════════════════ cenários 3 e 4 (extrato, cartão, vouchers) ═════════════════════════


def _csv_linhas(resp: requests.Response) -> List[List[str]]:
    """CSVs da Machine dizem UTF-8 mas vêm em Latin-1; separador ';'."""
    bruto = resp.content or b""
    try:
        texto = bruto.decode("utf-8")
    except UnicodeDecodeError:
        texto = bruto.decode("latin-1")
    return [l.split(";") for l in texto.strip().splitlines() if l.strip()]


def _coluna(cab: List[str], *nomes: str) -> int:
    norm = [re.sub(r"\s+", " ", c).strip().lower() for c in cab]
    for n in nomes:
        if n.lower() in norm:
            return norm.index(n.lower())
    raise ErroFinanceiro("csv_sem_coluna", f"CSV da Machine sem a coluna {nomes[0]!r} (colunas: {cab}).")


EXTRATO_ABAS = {"C": "corridas", "B": "beneficios", "R": "recargas", "S": "saques"}


def obter_extrato(http: requests.Session, ano_mes: str, hoje: Optional[date] = None) -> Dict[str, Any]:
    """
    Financeiro › Créditos › Extrato (central ATIVA). Uma chamada por aba, cada uma já traz os totais
    em response.estatisticas: Corridas (tx_central, cartao, cupom, ticket...), Benefícios (desafios,
    indicacoes, cashback), Recargas (recarga_central, recarga_pix), Saques (saque_central, saque_pix).
    """
    ano, mes = validar_ano_mes(ano_mes)
    if ano_mes == mes_passado_sp(hoje):
        periodo, ini, fim = "LM", "", ""
    else:
        periodo, ini, fim = "CT", f"{ano:04d}-{mes:02d}-01", f"{ano:04d}-{mes:02d}-{monthrange(ano, mes)[1]:02d}"
    totais: Dict[str, float] = {}
    por_aba: Dict[str, Dict[str, float]] = {}
    for tipo, nome in EXTRATO_ABAS.items():
        params = {
            "tipo": tipo, "subtipo": "tipo", "periodo": periodo, "data_ini": ini, "data_fim": fim,
            "taxista_id": "", "taxista_nome": "", "empresa_id": "0", "empresa_nome": "", "solicitacao_id": "",
            "categoria_id": "0", "tipo_pagamento_id": "0", "cliente_nome": "", "report_id": "", "subfiltros": "",
            "pageSize": "1", "pageNumber": "1",
        }
        r = http.get(BASE_URL + "/machinePay/obterExtratoMotoristas", params=params,
                     headers={"X-Requested-With": "XMLHttpRequest", "Referer": BASE_URL + "/machinePay/extrato"},
                     timeout=60)
        if _sessao_expirada(r):
            raise ErroFinanceiro("sessao_expirada", "Sessão da Machine expirou. Faça login novamente.", 401)
        data = _parse_json_response(r)
        if not data.get("success"):
            raise ErroFinanceiro("extrato_falhou", data.get("message") or f"Extrato ({nome}) recusado pela Machine.")
        est = (data.get("response") or {}).get("estatisticas") or {}
        aba = {k: parse_numero_br(v) for k, v in est.items() if isinstance(v, str) and "R$" in v}
        por_aba[nome] = {k: v for k, v in aba.items() if v}
        for k, v in aba.items():
            if v and not totais.get(k):
                totais[k] = v
    periodo_txt = (((data.get("response") or {}).get("filtros") or {}).get("periodo") or {}).get("valor")
    return {"totais": totais, "por_aba": por_aba, "periodo": periodo_txt}


def obter_cartao_liquido(http: requests.Session, bandeira_matriz: str, bandeira_corrida: str, ano_mes: str,
                         hoje: Optional[date] = None) -> Dict[str, Any]:
    """
    Financeiro › Cartão › Transações (pela MATRIZ, filtrando "Bandeira da corrida"), situações Paga +
    Em disputa + Chargeback (sem Recusada/Estornada) → exporta CSV → soma "Valor líquido da bandeira".
    """
    ano, mes = validar_ano_mes(ano_mes)
    ini, fim = f"01/{mes:02d}/{ano}", f"{monthrange(ano, mes)[1]:02d}/{mes:02d}/{ano}"
    F = "PagamentoSolicitacaoFilterForm"
    dados = [
        (f"{F}[solicitacao_id]", ""),
        (f"{F}[periodo]", "mes_passado" if ano_mes == mes_passado_sp(hoje) else "personalizado"),
        (f"{F}[data_inicial]", ini), ("data_inicial2", ini), (f"{F}[data_final]", fim), ("data_final2", fim),
        (f"{F}[bandeira_chamada_id]", str(bandeira_matriz)), (f"{F}[bandeira_corrida_id]", str(bandeira_corrida)),
        (f"{F}[nomeTaxista]", ""), (f"{F}[situacao]", ""),
        (f"{F}[situacao][]", "paga"), (f"{F}[situacao][]", "disputa"), (f"{F}[situacao][]", "chargeback"),
        ("export", ""),
    ]
    r = http.post(BASE_URL + "/cartao/exportTransacaoExcel", data=dados,
                  headers={**_HEADERS_PAGINA, "Referer": BASE_URL + "/cartao/transacao"}, timeout=180)
    if _sessao_expirada(r):
        raise ErroFinanceiro("sessao_expirada", "Sessão da Machine expirou. Faça login novamente.", 401)
    if "csv" not in (r.headers.get("Content-Type") or "").lower():
        raise ErroFinanceiro("cartao_export_falhou", f"Exportação de transações não devolveu CSV (HTTP {r.status_code}).")
    linhas = _csv_linhas(r)
    if not linhas:
        return {"total": 0.0, "linhas": 0, "situacoes": {}, "valor_corridas": 0.0}
    cab, corpo = linhas[0], linhas[1:]
    i_liq = _coluna(cab, "Valor líquido da bandeira")
    i_val = _coluna(cab, "Valor da corrida")
    i_sit = _coluna(cab, "Situação do Pagamento")
    i_ban = _coluna(cab, "Bandeira da corrida")
    total = valor = 0.0
    situacoes: Dict[str, int] = {}
    bandeiras: Dict[str, int] = {}
    for c in corpo:
        if len(c) <= max(i_liq, i_val, i_sit):
            continue
        total += parse_numero_br(c[i_liq])
        valor += parse_numero_br(c[i_val])
        situacoes[c[i_sit]] = situacoes.get(c[i_sit], 0) + 1
        bandeiras[c[i_ban]] = bandeiras.get(c[i_ban], 0) + 1
    return {"total": round(total, 2), "linhas": len(corpo), "situacoes": situacoes,
            "valor_corridas": round(valor, 2), "bandeiras_da_corrida": bandeiras}


def obter_vouchers_tickets(http: requests.Session, ano_mes: str) -> Dict[str, Any]:
    """Financeiro › Contas a pagar › Vouchers e tickets por motoristas (central ATIVA) → CSV → soma Ticket."""
    ano, mes = validar_ano_mes(ano_mes)
    dados = {
        "TaxistaFilterForm[first_date]": f"01/{mes:02d}/{ano}",
        "TaxistaFilterForm[last_date]": f"{monthrange(ano, mes)[1]:02d}/{mes:02d}/{ano}",
        "TaxistaFilterForm[nome]": "", "TaxistaFilterForm[vtr]": "", "export": "",
    }
    r = http.post(BASE_URL + "/financeiro/voucherTicketTaxista/index", data=dados,
                  headers={**_HEADERS_PAGINA, "Referer": BASE_URL + "/financeiro/voucherTicketTaxista/index"},
                  timeout=120)
    if _sessao_expirada(r):
        raise ErroFinanceiro("sessao_expirada", "Sessão da Machine expirou. Faça login novamente.", 401)
    if "csv" not in (r.headers.get("Content-Type") or "").lower():
        raise ErroFinanceiro("vouchers_export_falhou", f"Exportação de vouchers não devolveu CSV (HTTP {r.status_code}).")
    linhas = _csv_linhas(r)
    if len(linhas) <= 1:
        return {"ticket": 0.0, "voucher": 0.0, "motoristas": 0}
    cab, corpo = linhas[0], linhas[1:]
    i_t, i_v = _coluna(cab, "Ticket"), _coluna(cab, "Voucher")
    ticket = sum(parse_numero_br(c[i_t]) for c in corpo if len(c) > i_t)
    voucher = sum(parse_numero_br(c[i_v]) for c in corpo if len(c) > i_v)
    return {"ticket": round(ticket, 2), "voucher": round(voucher, 2), "motoristas": len(corpo)}


def calcular_cenario_03(entradas: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Cenário 3 (vídeo "COBRANÇA ROYALTIES - CENÁRIO 3"):
      entradas = recarga via Central + recarga via Pix + cupons + desafios + indicações + cashback
                 + cartão no app BRUTO (soma do "Valor líquido da bandeira" das transações)
                 + eTicket (soma da coluna Ticket em Vouchers e tickets por motoristas)
      saídas   = saque via Central + saque via Pix + seguro app
      royalty  = (entradas − saídas) × % da Ubiz
    O cartão no app e o eTicket do extrato NÃO entram (são o líquido creditado ao motorista).
    """
    ext = entradas["extrato"]
    g = lambda k: float(ext.get(k) or 0)  # noqa: E731
    itens_entrada = [
        ("Recarga via Central", g("recarga_central")),
        ("Recarga via Pix", g("recarga_pix")),
        ("Cupons", g("cupom")),
        ("Desafios", g("desafios")),
        ("Indicações", g("indicacoes")),
        ("Cashback e bônus", g("cashback")),
        ("Cartão no app (transações, líquido da bandeira)", float(entradas["cartao_liquido"])),
        ("eTicket (vouchers e tickets por motoristas)", float(entradas["eticket"])),
    ]
    avisos: List[str] = []
    seguro = calcular_seguro_app(float(entradas.get("corridas_finalizadas") or 0), cfg)
    if seguro["origem"] == "nao_configurada":
        avisos.append("Seguro app sem taxa configurada: descontado R$ 0,00.")
    itens_saida = [("Saque via Central", g("saque_central")), ("Saque via Pix", g("saque_pix")),
                   ("Seguro app", seguro["valor"])]
    total_ent = sum(v for _, v in itens_entrada)
    total_sai = sum(v for _, v in itens_saida)
    base = total_ent - total_sai
    return _fechar_royalty("cenario_03", base, cfg, avisos, {
        "entradas": [{"item": n, "valor": round(v, 2)} for n, v in itens_entrada],
        "saidas": [{"item": n, "valor": round(v, 2)} for n, v in itens_saida],
        "total_entradas": round(total_ent, 2),
        "total_saidas": round(total_sai, 2),
        "seguro_app": seguro["valor"],
        "seguro_app_detalhe": seguro,
        "valor_bruto": round(total_ent, 2),
    })


def calcular_cenario_04(entradas: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Cenário 4 (vídeo "COBRANÇA ROYALTIES - CENÁRIO 4"):
      taxa do cartão = transações (líquido da bandeira) − "Cartão no App" do extrato
      royalty        = (Taxa da central do extrato + taxa do cartão) × % da Ubiz
    """
    ext = entradas["extrato"]
    tx_central = float(ext.get("tx_central") or 0)
    cartao_extrato = float(ext.get("cartao") or 0)
    cartao_liq = float(entradas["cartao_liquido"])
    taxa_cartao = cartao_liq - cartao_extrato
    avisos: List[str] = []
    if taxa_cartao < 0:
        avisos.append("Cartão: transações menores que o 'Cartão no App' do extrato (taxa do cartão negativa).")
    base = tx_central + taxa_cartao
    return _fechar_royalty("cenario_04", base, cfg, avisos, {
        "taxa_central_extrato": round(tx_central, 2),
        "cartao_transacoes": round(cartao_liq, 2),
        "cartao_no_app_extrato": round(cartao_extrato, 2),
        "taxa_cartao": round(taxa_cartao, 2),
        "valor_bruto": round(base, 2),
    })


def _fechar_royalty(cenario: str, base: float, cfg: Dict[str, Any], avisos: List[str],
                    extra: Dict[str, Any]) -> Dict[str, Any]:
    if base < 0:
        avisos.append("Base negativa.")
    taxa_valor = float(cfg.get("taxa_valor") or 0)
    taxa_tipo = cfg.get("taxa_tipo") or "percentual"
    if taxa_valor <= 0:
        avisos.append("Cidade sem % de royalty configurada.")
    royalty = _aplicar(base, taxa_valor, taxa_tipo)
    # Cenários 3 e 4: o financeiro não somou tarifa de boleto nos vídeos; só soma se a config pedir.
    tarifa = _aplicar(base, float(cfg.get("tarifa_boleto") or 0), cfg.get("tarifa_boleto_tipo") or "fixo") \
        if cfg.get("somar_tarifa_boleto") else 0.0
    return {
        "cenario": cenario,
        **extra,
        "saldo_01": round(base, 2),
        "base_royalties": round(base, 2),
        "taxa_cidade": taxa_valor,
        "taxa_cidade_tipo": taxa_tipo,
        "royalty": round(royalty, 2),
        "tarifa_boleto": round(tarifa, 2),
        "valor_final": round(royalty + tarifa, 2),
        "avisos": avisos,
    }


def coletar_cidade_extrato(
    http: requests.Session,
    bandeira_id: str,
    ano_mes: str,
    cenario: str,
    cartao: Dict[str, Any],
    trocar: bool = True,
) -> Dict[str, Any]:
    """
    Cenários 3 e 4. `cartao` (transações) é lido ANTES, pela matriz. Aqui: troca de central →
    taxas (só para confirmar a central) → [cenário 3: dispara filtro de corridas p/ seguro] →
    extrato → [cenário 3: vouchers] → [cenário 3: espera o filtro].
    """
    tempos: Dict[str, float] = {}
    t = time.time()

    def marca(etapa: str) -> None:
        nonlocal t
        agora = time.time()
        tempos[etapa] = round(agora - t, 2)
        t = agora

    avisos: List[str] = []
    if trocar:
        trocar_central(http, bandeira_id, confirmar=False)
        marca("troca_central")
        taxas = obter_taxas_bandeira(http)
        ativa = taxas.get("_central_ativa")
        if ativa is None:
            confirmar_central(listar_centrais_html(http), bandeira_id)
        elif ativa != str(bandeira_id):
            raise ErroFinanceiro("troca_central_falhou", f"Não foi possível trocar para a central {bandeira_id} (atual: {ativa}).")
        marca("taxas")
    filtro_iniciado, erro_filtro = None, None
    if cenario == "cenario_03":
        try:
            filtro_iniciado = iniciar_filtro_corridas(http, ano_mes)
        except ErroFinanceiro as exc:
            if exc.codigo == "sessao_expirada":
                raise
            erro_filtro = exc
    extrato = obter_extrato(http, ano_mes)
    marca("extrato")
    entradas: Dict[str, Any] = {
        "cenario": cenario,
        "extrato": extrato["totais"],
        "cartao_liquido": cartao["total"],
    }
    vouchers, filtro = None, None
    if cenario == "cenario_03":
        vouchers = obter_vouchers_tickets(http, ano_mes)
        marca("vouchers")
        entradas["eticket"] = vouchers["ticket"]
        try:
            if erro_filtro:
                raise erro_filtro
            filtro = concluir_filtro_corridas(http, filtro_iniciado)
            marca("filtro_corridas")
            entradas["corridas_finalizadas"] = filtro["total"]
        except ErroFinanceiro as exc:
            if exc.codigo == "sessao_expirada":
                raise
            filtro = {"erro": str(exc)}
            entradas["corridas_finalizadas"] = 0
            avisos.append(f"Filtro de Consultar corridas falhou ({exc}): seguro app ficou R$ 0,00.")
    return {
        "extrato": extrato,
        "cartao": cartao,
        "vouchers": vouchers,
        "consultar_corridas": filtro,
        "avisos": avisos,
        "tempos_s": tempos,
        "entradas_calculo": entradas,
    }


# compatibilidade com o nome antigo
calcular_cenario_01 = calcular_cenario
