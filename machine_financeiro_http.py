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


def trocar_central(http: requests.Session, bandeira_id: str) -> None:
    """Equivale a escolher a cidade no seletor do topo do painel."""
    bandeira_id = str(bandeira_id)
    http.post(
        BASE_URL + "/site/trocarCentral",
        data={"bandeiraId": bandeira_id},
        headers={**_HEADERS_PAGINA, "Referer": BASE_URL + "/demonstrativoMensal/index"},
        timeout=60,
    )
    atual = next((c for c in listar_centrais(http) if c["atual"]), None)
    if not atual or atual["bandeira_id"] != bandeira_id:
        raise ErroFinanceiro(
            "troca_central_falhou",
            f"Não foi possível trocar para a central {bandeira_id} (atual: {atual and atual['bandeira_id']}).",
        )


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


def obter_demonstrativo(http: requests.Session, bandeira_id: str, ano_mes: str) -> Dict[str, Any]:
    """
    "Boleto da Machine". Funciona com a sessão na central da MATRIZ (seletor de bandeiras).
    Tenta POST do formulário (como o painel) e, se não vier tabela, GET com os mesmos parâmetros.
    """
    validar_ano_mes(ano_mes)
    r_get = _get_pagina(http, "/demonstrativoMensal/index")
    form = re.search(r"<form[^>]*action=\"[^\"]*demonstrativoMensal/index\"[^>]*>(.*?)</form>", r_get.text, re.S)
    dados = _inputs_ocultos(form.group(1) if form else "")
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


def calcular_cenario_01(entradas: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    entradas: ganhos_gerais, taxa_central_pct, corridas_finalizadas,
              itens_demonstrativo (quantidades da Machine), boleto_machine (só referência)
    cfg: taxa_valor, taxa_tipo (royalty Ubiz da cidade), taxa_seguro_app[_tipo],
         taxa_seguro_app_global[_tipo], tarifa_boleto, tarifa_boleto_tipo, tabela_ubiz

    Regra do financeiro Ubiz:
      base = ganhos × taxa_central − boleto_ubiz − seguro_app
      valor_final = base × % da cidade + tarifa de boleto
    """
    avisos: List[str] = []
    ganho = float(entradas["ganhos_gerais"])
    pct_central = float(entradas["taxa_central_pct"])
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

    saldo_01 = ganho * pct_central / 100.0
    seguro = calcular_seguro_app(float(entradas.get("corridas_finalizadas") or 0), cfg)
    if seguro["origem"] == "nao_configurada":
        avisos.append("Seguro app sem taxa configurada (cidade nem global): descontado R$ 0,00.")
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
        "ganhos_gerais": r2(ganho),
        "taxa_central_pct": pct_central,
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
) -> Dict[str, Any]:
    """
    Busca na Machine tudo que o cálculo precisa, para uma cidade.
    `demonstrativo` deve ser lido ANTES da troca de central (a tela só funciona na matriz).
    """
    dem = demonstrativo if demonstrativo is not None else obter_demonstrativo(http, bandeira_id, ano_mes)
    if trocar:
        trocar_central(http, bandeira_id)
    ganhos = obter_ganhos_mes(http, ano_mes, bandeira_id=bandeira_id)
    taxas = obter_taxas_bandeira(http)
    pct = taxas["taxa_central"].get("percentual")
    if pct is None:
        raise ErroFinanceiro("taxa_central_sem_percentual", "Taxa da central não é percentual; cenário 01 não se aplica.")
    return {
        "ganhos": ganhos,
        "taxas": taxas,
        "demonstrativo": dem,
        "entradas_calculo": {
            "ganhos_gerais": ganhos["ganhos_gerais"],
            "taxa_central_pct": pct,
            "boleto_machine": dem["valor_a_pagar"],
            "itens_demonstrativo": dem["itens"],
            "corridas_finalizadas": ganhos["corridas_finalizadas"],
        },
    }
