"""
Relatórios assíncronos do painel TaxiMachine (botão "Exportar").

Mesma sessão HTTP (PHPSESSID) de /notificacao/login. O painel gera o arquivo em
background e devolve uma URL S3 pré-assinada (~1 h) quando `statusExport == ready`.
O PHPSESSID fica preso ao IP de quem logou: login, solicitação e polling rodam aqui.
"""

from __future__ import annotations

import calendar
import logging
import re
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

from machine_notificacao_http import (
    BASE_URL,
    STATUS_EXPORT_PENDENTE,
    STATUS_EXPORT_PRONTO,
)

log = logging.getLogger(__name__)

RELATORIOS: Dict[str, Dict[str, Any]] = {
    "clientes": {
        "solicitar": "/cliente/solicitarExportarRelatorioClientes/",
        "status": "/cliente/verificarStatusRelatorioClientes/{id}",
        "referer": "/cliente/index",
        "filtros_padrao": {
            "nome": "",
            "telefone": "",
            "email": "",
            "cpf": "",
            "status_cliente": "null",
            "tipo_cliente": "",
            "bandeira_configuracao_id": "",
            "incluir_cliente_com_cartao_nao_validado": "",
            "criado_em_ini": "",
            "criado_em_fim": "",
            "data_nascimento_ini": "",
            "data_nascimento_fim": "",
        },
    },
}

AGUARDAR_MAX_SEG = 240


def _config(tipo: str) -> Dict[str, Any]:
    cfg = RELATORIOS.get(tipo)
    if not cfg:
        raise RuntimeError(f"Tipo de relatório desconhecido: {tipo}")
    return cfg


def _headers(cfg: Dict[str, Any]) -> Dict[str, str]:
    return {
        "Referer": BASE_URL + cfg["referer"],
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }


def _json_ou_sessao_expirada(r: requests.Response) -> Dict[str, Any]:
    texto = (r.text or "").lstrip()
    if texto.startswith("<") or "LoginForm" in texto[:4000]:
        raise RuntimeError("Sessão inválida ou expirada — faça login novamente.")
    try:
        return r.json()
    except Exception as exc:
        raise RuntimeError(f"Resposta não é JSON (HTTP {r.status_code}): {texto[:300]}") from exc


def _expira_em_url(url: Optional[str]) -> Optional[int]:
    """Epoch (s) em que o link S3 pré-assinado expira."""
    if not url:
        return None
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    try:
        inicio = calendar.timegm(time.strptime(q["X-Amz-Date"], "%Y%m%dT%H%M%SZ"))
        return int(inicio + int(q["X-Amz-Expires"]))
    except (KeyError, ValueError):
        return None


def solicitar_relatorio(
    http: requests.Session,
    tipo: str,
    filtros: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    cfg = _config(tipo)
    dados = dict(cfg["filtros_padrao"])
    for k, v in (filtros or {}).items():
        if k in dados and v is not None:
            dados[k] = str(v)

    r = http.post(BASE_URL + cfg["solicitar"], data=dados, headers=_headers(cfg), timeout=60)
    data = _json_ou_sessao_expirada(r)
    report_id = data.get("report_id")
    if not data.get("success") or not report_id:
        msg = data.get("message") or data.get("mensagem") or "Painel recusou a exportação."
        raise RuntimeError(f"{msg} (HTTP {r.status_code})")

    log.info("relatorio %s solicitado report_id=%s", tipo, report_id)
    return {
        "report_id": int(report_id),
        "status_export": data.get("statusExport"),
        "tipo_processamento": data.get("tipo"),
    }


def verificar_relatorio(http: requests.Session, tipo: str, report_id: int | str) -> Dict[str, Any]:
    cfg = _config(tipo)
    url_status = BASE_URL + cfg["status"].format(id=report_id)
    r = http.get(url_status, headers=_headers(cfg), timeout=30)
    data = _json_ou_sessao_expirada(r)
    status = data.get("statusExport") or data.get("status")
    url = data.get("url") or None
    pronto = status in STATUS_EXPORT_PRONTO and bool(url)
    return {
        "report_id": int(report_id),
        "tipo": tipo,
        "status_export": status,
        "pronto": pronto,
        "pendente": status in STATUS_EXPORT_PENDENTE or (status in STATUS_EXPORT_PRONTO and not url),
        "cancelado": status == "canceled",
        "url": url if pronto else None,
        "url_expira_em": _expira_em_url(url) if pronto else None,
    }


def aguardar_relatorio_tipo(
    http: requests.Session,
    tipo: str,
    report_id: int | str,
    timeout_seg: int = 120,
    intervalo_seg: float = 10.0,
) -> Dict[str, Any]:
    timeout_seg = max(0, min(int(timeout_seg), AGUARDAR_MAX_SEG))
    deadline = time.time() + timeout_seg
    ultimo = verificar_relatorio(http, tipo, report_id)
    while not ultimo["pronto"] and not ultimo["cancelado"] and time.time() < deadline:
        time.sleep(min(intervalo_seg, max(0.0, deadline - time.time())))
        ultimo = verificar_relatorio(http, tipo, report_id)
    if ultimo["cancelado"]:
        raise RuntimeError(f"Geração do relatório {report_id} foi cancelada pelo painel.")
    return ultimo


# ── Corridas (/solicitacao/historicoCorridas2) ─────────────────────────────
# Fluxo: filtrar (relatorioCorridas) → status=ready → exportar (report=ID) →
# statusExport=ready + url. O filtro fica na sessão PHP: uma janela por vez.

CORRIDAS_REFERER = "/solicitacao/historicoCorridas2"
CORRIDAS_MAX_DIAS_JANELA = 31
CORRIDAS_QTD_JANELAS = 3

_CAMPOS_FILTRO_CORRIDAS = (
    "id",
    "id_externo",
    "bandeira_chamada_id",
    "bandeira_corrida_id",
    "empresa_id",
    "filtro_categoria_id",
    "nome_passageiro",
    "telefone_passageiro",
    "cpf_passageiro",
    "endereco_partida",
    "nomeTaxista",
    "inicio_corrida",
    "hora_inicial",
    "final_corrida",
    "hora_final",
    "payment_type",
    "status_solicitacao",
    "nivel_urgencia",
    "origem_solicitacao",
    "cancelado_por",
    "report",
    "corridas_cashback",
)


def hoje_sao_paulo() -> date:
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/Sao_Paulo")
    except Exception:
        tz = timezone(timedelta(hours=-3))  # sem horário de verão desde 2019
    return datetime.now(tz).date()


def _somar_meses(d: date, meses: int) -> date:
    m = d.month - 1 + meses
    ano, mes = d.year + m // 12, m % 12 + 1
    return date(ano, mes, min(d.day, calendar.monthrange(ano, mes)[1]))


def calcular_janelas_corridas(
    hoje: Optional[date] = None,
    qtd: int = CORRIDAS_QTD_JANELAS,
) -> List[Dict[str, date]]:
    """
    Janelas mensais contíguas terminando ontem (o dia atual está incompleto).
    Ex.: hoje 02/10 → 02/09–01/10, 02/08–01/09, 02/07–01/08 (mais recente primeiro).
    """
    hoje = hoje or hoje_sao_paulo()
    janelas = []
    for k in range(max(1, int(qtd))):
        inicio = _somar_meses(hoje, -(k + 1))
        fim = _somar_meses(hoje, -k) - timedelta(days=1)
        dias = (fim - inicio).days + 1
        if dias > CORRIDAS_MAX_DIAS_JANELA:
            raise RuntimeError(f"Janela {inicio}..{fim} tem {dias} dias (máx. {CORRIDAS_MAX_DIAS_JANELA}).")
        janelas.append({"inicio": inicio, "fim": fim})
    return janelas


def _headers_corridas() -> Dict[str, str]:
    return {
        "Referer": BASE_URL + CORRIDAS_REFERER,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }


def _form_corridas(inicio: date, fim: date, report: Optional[int | str] = None) -> Dict[str, str]:
    campos = {c: "" for c in _CAMPOS_FILTRO_CORRIDAS}
    campos.update(
        inicio_corrida=inicio.strftime("%d/%m/%Y"),
        hora_inicial="00:00",
        final_corrida=fim.strftime("%d/%m/%Y"),
        hora_final="23:59",
        corridas_cashback="0",
        report="" if report is None else str(report),
    )
    form = {f"HistoricoFilterForm[{k}]": v for k, v in campos.items()}
    form["tipo"] = "corrida"
    return form


_RE_MY_USER = re.compile(r"MY_USER\s*[=:]\s*['\"]?(\d+)")


def _resumo_pagina(r: requests.Response) -> str:
    texto = r.text or ""
    titulo = re.search(r"<title[^>]*>(.*?)</title>", texto, re.I | re.S)
    t = re.sub(r"\s+", " ", titulo.group(1)).strip()[:80] if titulo else "sem título"
    caminho = re.sub(r"^https?://[^/]+", "", r.url or "")[:80]
    return f"HTTP {r.status_code} em {caminho or '?'} ({t})"


def obter_my_user(http: requests.Session) -> Optional[str]:
    """
    ID do usuário logado (`MY_USER` no JS da página), usado no polling de status.
    Algumas contas não expõem a variável: devolve None e o polling segue sem `user`.
    """
    tentativas = (
        (CORRIDAS_REFERER, {"resetSesion": "1"}),
        (CORRIDAS_REFERER, None),
        ("/", None),
    )
    resumos = []
    for caminho, params in tentativas:
        r = http.get(
            BASE_URL + caminho,
            params=params,
            headers={"Referer": BASE_URL + CORRIDAS_REFERER},
            timeout=60,
        )
        if "site/login" in (r.url or "") or "LoginForm" in (r.text or "")[:20000]:
            raise RuntimeError("Sessão inválida ou expirada — faça login novamente.")
        if caminho == CORRIDAS_REFERER and (
            r.status_code == 403 or "não está autorizado" in (r.text or "")[:50000]
        ):
            raise RuntimeError(
                "Login da cidade sem permissão para o Histórico de corridas na Machine "
                f"({_resumo_pagina(r)})."
            )
        m = _RE_MY_USER.search(r.text or "")
        if m:
            return m.group(1)
        resumos.append(_resumo_pagina(r))
    log.warning("MY_USER não encontrado: %s", " | ".join(resumos))
    return None


def filtrar_corridas(http: requests.Session, inicio: date, fim: date) -> Dict[str, Any]:
    r = http.post(
        BASE_URL + "/solicitacao/relatorioCorridas",
        data=_form_corridas(inicio, fim),
        headers=_headers_corridas(),
        timeout=60,
    )
    data = _json_ou_sessao_expirada(r)
    report_id = data.get("report_id")
    if not data.get("success") or not report_id:
        msg = data.get("message") or data.get("mensagem") or "Painel recusou o filtro de corridas."
        raise RuntimeError(f"{msg} (HTTP {r.status_code})")
    log.info("corridas filtro %s..%s report_id=%s tipo=%s", inicio, fim, report_id, data.get("tipo"))
    return {
        "report_id": int(report_id),
        "sincrono": data.get("tipo") == "sincrono",
        "status": data.get("status"),
    }


def status_corridas(http: requests.Session, report_id: int | str, my_user: Optional[str]) -> Dict[str, Any]:
    params: Dict[str, Any] = {"report": report_id}
    if my_user:
        params["user"] = my_user
    r = http.get(
        BASE_URL + "/solicitacao/statusRelatorioCorridas",
        params=params,
        headers=_headers_corridas(),
        timeout=30,
    )
    data = _json_ou_sessao_expirada(r)
    if data.get("success") is False:
        msg = data.get("message") or data.get("mensagem") or "falha no processamento"
        raise RuntimeError(f"Relatório de corridas {report_id}: {msg}")
    status_export = data.get("statusExport")
    url = data.get("url") or None
    pronto = status_export in STATUS_EXPORT_PRONTO and bool(url)
    return {
        "report_id": int(report_id),
        "status": data.get("status"),
        "status_export": status_export,
        "filtro_pronto": data.get("status") in STATUS_EXPORT_PRONTO,
        "pronto": pronto,
        "cancelado": "canceled" in (data.get("status"), status_export),
        "url": url if pronto else None,
        "url_expira_em": _expira_em_url(url) if pronto else None,
    }


def exportar_corridas(http: requests.Session, inicio: date, fim: date, report_id: int | str) -> Dict[str, Any]:
    r = http.post(
        BASE_URL + "/solicitacao/exportarRelatorioCorridas",
        data=_form_corridas(inicio, fim, report_id),
        headers=_headers_corridas(),
        timeout=60,
    )
    data = _json_ou_sessao_expirada(r)
    if not data.get("success"):
        msg = data.get("message") or data.get("mensagem") or "Painel recusou a exportação de corridas."
        raise RuntimeError(f"{msg} (HTTP {r.status_code})")
    url = data.get("url") or None
    return {
        "report_id": int(data.get("report_id") or report_id),
        "status_export": data.get("statusExport"),
        "url": url,
        "url_expira_em": _expira_em_url(url),
    }
