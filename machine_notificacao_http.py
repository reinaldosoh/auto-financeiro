"""
Cliente HTTP para o painel TaxiMachine — campanhas em /notificacao/create.

Autenticação por cookie de sessão (PHPSESSID), não usa Selenium nem a API integracao/v1.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://cloud.taximachine.com.br"
SESSION_TTL_SECONDS = 30 * 60

_sessions: Dict[str, Dict[str, Any]] = {}
_sessions_lock = threading.Lock()


def _now() -> float:
    return time.time()


def _cleanup_sessions() -> None:
    cutoff = _now() - SESSION_TTL_SECONDS
    expired = [token for token, item in _sessions.items() if item["created_at"] < cutoff]
    for token in expired:
        _sessions.pop(token, None)


def _new_session(email: str) -> tuple[str, requests.Session]:
    token = str(uuid.uuid4())
    http = requests.Session()
    http.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    with _sessions_lock:
        _cleanup_sessions()
        _sessions[token] = {
            "email": email,
            "http": http,
            "created_at": _now(),
        }
    return token, http


def get_session(token: str) -> Optional[requests.Session]:
    with _sessions_lock:
        _cleanup_sessions()
        item = _sessions.get(token)
        if not item:
            return None
        item["created_at"] = _now()
        return item["http"]


def _parse_json_response(response: requests.Response) -> Dict[str, Any]:
    try:
        return response.json()
    except Exception as exc:
        snippet = (response.text or "")[:300]
        raise RuntimeError(f"Resposta não é JSON (HTTP {response.status_code}): {snippet}") from exc


def login_painel(
    email: str,
    senha: str,
    codigo_2fa: Optional[str] = None,
    chave_secreta: Optional[str] = None,
    gerar_codigo_fn=None,
) -> Dict[str, Any]:
    """
    Login HTTP no painel. Retorna session_token + metadados da conta.
    """
    token, http = _new_session(email)

    try:
        http.get(BASE_URL + "/")

        login_data = {
            "LoginForm[username]": email,
            "LoginForm[password]": senha,
            "LoginForm[rememberMe]": "0",
        }
        r = http.post(
            BASE_URL + "/site/login",
            data=login_data,
            headers={"Referer": BASE_URL + "/"},
            timeout=30,
        )
        data = _parse_json_response(r)

        if data.get("cadastrar2FA"):
            raise RuntimeError(
                "Conta exige cadastro de 2FA no painel antes do login HTTP."
            )

        if data.get("solicitarCodigo2FA"):
            code = codigo_2fa
            if not code and chave_secreta and gerar_codigo_fn:
                code = gerar_codigo_fn(chave_secreta)
            if not code and gerar_codigo_fn:
                from auto_2fa import obter_chave

                chave_salva = obter_chave(email)
                if chave_salva:
                    code = gerar_codigo_fn(chave_salva)

            if not code:
                raise RuntimeError(
                    "Conta exige código 2FA. Informe codigo_2fa ou chave_secreta."
                )

            r2 = http.post(
                BASE_URL + "/site/verificar2FA",
                data={"code": code},
                headers={"Referer": BASE_URL + "/"},
                timeout=30,
            )
            data2 = _parse_json_response(r2)
            if not data2.get("success"):
                msg = data2.get("message") or "Falha ao verificar 2FA."
                raise RuntimeError(msg)

        elif not data.get("success"):
            errors = {k: v for k, v in data.items() if k.startswith("LoginForm")}
            raise RuntimeError(errors or "Credenciais inválidas.")

        bandeiras = obter_bandeiras(http)
        phpsessid = http.cookies.get("PHPSESSID", "")

        return {
            "sucesso": True,
            "email": email,
            "session_token": token,
            "phpsessid": phpsessid,
            "bandeiras": bandeiras,
            "mensagem": "Login HTTP no painel concluído.",
        }
    except Exception:
        with _sessions_lock:
            _sessions.pop(token, None)
        raise


def obter_bandeiras(http: requests.Session) -> List[Dict[str, str]]:
    """Extrai centrais disponíveis da página /notificacao/create (LISTA_FUSOS)."""
    r = http.get(BASE_URL + "/notificacao/create", timeout=30)
    if "LoginForm" in r.text and "notificacao-form" not in r.text:
        raise RuntimeError("Sessão inválida ou expirada.")

    match = re.search(r"const LISTA_FUSOS\s*=\s*(\[[^\]]*\])", r.text)
    if not match:
        bandeira_match = re.search(r"bandeiraId:\s*(\d+)", r.text)
        if bandeira_match:
            return [{"id": bandeira_match.group(1), "fuso_horario": ""}]
        return []

    try:
        items = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []

    return [
        {"id": str(item.get("id", "")), "fuso_horario": item.get("fuso_horario", "")}
        for item in items
        if item.get("id")
    ]


def obter_categorias(
    http: requests.Session,
    bandeira_id: Optional[str] = None,
) -> Dict[str, Any]:
    """GET /notificacao/obterCategorias — mesma chamada do front do painel."""
    params = {"bandeira_id": bandeira_id or ""}
    r = http.get(
        BASE_URL + "/notificacao/obterCategorias",
        params=params,
        timeout=30,
    )

    if r.text.strip().startswith("<!DOCTYPE") or r.text.strip().startswith("<html"):
        raise RuntimeError("Sessão expirada — faça login novamente.")

    data = _parse_json_response(r)
    if not data.get("success"):
        return {
            "sucesso": False,
            "bandeira_id": bandeira_id or "",
            "categorias": [],
            "mensagem": "Nenhuma categoria encontrada para esta central.",
        }

    categorias = data.get("categorias") or []
    return {
        "sucesso": True,
        "bandeira_id": bandeira_id or "",
        "total": len(categorias),
        "categorias": categorias,
        "mensagem": f"{len(categorias)} categoria(s) encontrada(s).",
    }


def obter_categorias_com_credenciais(
    email: str,
    senha: str,
    bandeira_id: Optional[str] = None,
    codigo_2fa: Optional[str] = None,
    chave_secreta: Optional[str] = None,
    gerar_codigo_fn=None,
) -> Dict[str, Any]:
    login = login_painel(
        email=email,
        senha=senha,
        codigo_2fa=codigo_2fa,
        chave_secreta=chave_secreta,
        gerar_codigo_fn=gerar_codigo_fn,
    )
    http = get_session(login["session_token"])
    if not http:
        raise RuntimeError("Falha ao recuperar sessão após login.")

    result = obter_categorias(http, bandeira_id=bandeira_id)
    result["email"] = email
    result["session_token"] = login["session_token"]
    result["bandeiras"] = login["bandeiras"]
    return result
