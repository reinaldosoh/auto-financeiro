"""
Relatórios assíncronos do painel TaxiMachine (botão "Exportar").

Mesma sessão HTTP (PHPSESSID) de /notificacao/login. O painel gera o arquivo em
background e devolve uma URL S3 pré-assinada (~1 h) quando `statusExport == ready`.
O PHPSESSID fica preso ao IP de quem logou: login, solicitação e polling rodam aqui.
"""

from __future__ import annotations

import calendar
import logging
import time
import urllib.parse
from typing import Any, Dict, Optional

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
