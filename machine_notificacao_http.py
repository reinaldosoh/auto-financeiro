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
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://cloud.taximachine.com.br"
SESSION_TTL_SECONDS = 30 * 60

STATUS_EXPORT_PRONTO = frozenset({"ready", "finalizado"})
STATUS_EXPORT_PENDENTE = frozenset(
    {"generate", "gerando", "processing", "processando", "not_started", "nao_iniciado", "new"}
)

_sessions: Dict[str, Dict[str, Any]] = {}
_sessions_lock = threading.Lock()


@dataclass
class NotificacaoForm:
    titulo: str = ""
    mensagem: str = ""
    destinatario: str = "D"
    bandeira_id: Optional[str] = None
    periodo: str = "hoje"
    quantidade_corridas_operacao: str = "N"
    quantidade_corridas: str = "0"
    periodo_cadastro: str = "selecione"
    data_inicial: str = ""
    data_final: str = ""
    data_inicial_cadastro: str = ""
    data_final_cadastro: str = ""
    categorias: List[str] = field(default_factory=list)
    generos: List[str] = field(default_factory=list)
    status_taxi: str = ""
    sistema_operacional_android: str = "0"
    sistema_operacional_ios: str = "0"
    cliente_id: str = ""
    data_envio: str = ""
    hora_envio: str = ""

    def to_form_pairs(self) -> List[Tuple[str, str]]:
        pairs: List[Tuple[str, str]] = [
            ("NotificacaoPushForm[titulo]", self.titulo or ""),
            ("NotificacaoPushForm[mensagem]", self.mensagem or ""),
            ("NotificacaoPushForm[destinatario]", self.destinatario or ""),
            (
                "NotificacaoPushForm[quantidade_corridas_operacao]",
                self.quantidade_corridas_operacao or "N",
            ),
            ("NotificacaoPushForm[quantidade_corridas]", self.quantidade_corridas or "0"),
            ("NotificacaoPushForm[periodo]", self.periodo or "hoje"),
            ("NotificacaoPushForm[periodo_cadastro]", self.periodo_cadastro or "selecione"),
            (
                "NotificacaoPushForm[sistema_operacional_android]",
                self.sistema_operacional_android or "0",
            ),
            (
                "NotificacaoPushForm[sistema_operacional_ios]",
                self.sistema_operacional_ios or "0",
            ),
            ("NotificacaoPushForm[cliente_id]", self.cliente_id or ""),
            ("NotificacaoPushForm[status_taxi]", self.status_taxi or ""),
            ("NotificacaoPushForm[data_inicial]", self.data_inicial or ""),
            ("NotificacaoPushForm[data_final]", self.data_final or ""),
            ("NotificacaoPushForm[data_inicial_cadastro]", self.data_inicial_cadastro or ""),
            ("NotificacaoPushForm[data_final_cadastro]", self.data_final_cadastro or ""),
            ("NotificacaoPushForm[data_envio]", self.data_envio or ""),
            ("NotificacaoPushForm[hora_envio]", self.hora_envio or ""),
        ]
        if self.bandeira_id:
            pairs.append(
                ("NotificacaoPushForm[bandeira_selecionada_id]", str(self.bandeira_id))
            )
        for cat in self.categorias:
            pairs.append(("NotificacaoPushForm[categorias][]", str(cat)))
        for gen in self.generos:
            pairs.append(("NotificacaoPushForm[generos][]", str(gen)))
        return pairs


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


def get_session_email(token: str) -> Optional[str]:
    with _sessions_lock:
        _cleanup_sessions()
        item = _sessions.get(token)
        if not item:
            return None
        item["created_at"] = _now()
        return item.get("email")


def _parse_json_response(response: requests.Response) -> Dict[str, Any]:
    try:
        return response.json()
    except Exception as exc:
        snippet = (response.text or "")[:300]
        raise RuntimeError(
            f"Resposta não é JSON (HTTP {response.status_code}): {snippet}"
        ) from exc


def _extrair_js_json(html: str, var_name: str) -> Any:
    patterns = [
        rf"const {var_name}\s*=\s*(\{{.*?\}})\s*;",
        rf"const {var_name}\s*=\s*(\[.*?\])\s*;",
        rf"var {var_name}\s*=\s*(\{{.*?\}})\s*;",
    ]
    for pat in patterns:
        m = re.search(pat, html, re.S)
        if not m:
            continue
        raw = m.group(1).strip()
        if raw.startswith("parseInt"):
            inner = re.search(r"parseInt\(['\"](\d*)['\"]\)", raw)
            return int(inner.group(1)) if inner and inner.group(1) else None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            continue
    return None


def _post_create_form(
    http: requests.Session,
    form: NotificacaoForm,
    acao: str,
) -> requests.Response:
    pairs = form.to_form_pairs()
    pairs.append((acao, acao.replace("_", " ").title() if acao == "filtrar" else "Enviar notificação"))
    if acao == "filtrar":
        pairs[-1] = ("filtrar", "Avançar")
    elif acao == "criar":
        pairs[-1] = ("criar", "Enviar notificação")

    return http.post(
        BASE_URL + "/notificacao/create",
        data=pairs,
        headers={"Referer": BASE_URL + "/notificacao/create"},
        timeout=120,
    )


def login_painel(
    email: str,
    senha: str,
    codigo_2fa: Optional[str] = None,
    chave_secreta: Optional[str] = None,
    gerar_codigo_fn=None,
) -> Dict[str, Any]:
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
        chave_registrada: Optional[str] = None

        if data.get("cadastrar2FA"):
            auth2f = data.get("autenticacao2Fatores") or {}
            secret = str(auth2f.get("secret") or "").replace(" ", "")
            if not secret:
                raise RuntimeError(
                    "Cadastro 2FA exigido, mas o painel não retornou o segredo TOTP."
                )
            if not gerar_codigo_fn:
                from auto_2fa import gerar_codigo as _gerar

                gerar_codigo_fn = _gerar
            code = gerar_codigo_fn(secret)
            r_reg = http.post(
                BASE_URL + "/site/validarCadastro2FA",
                data={"code": code},
                headers={"Referer": BASE_URL + "/"},
                timeout=30,
            )
            data_reg = _parse_json_response(r_reg)
            if not data_reg.get("success"):
                msg = data_reg.get("message") or "Falha ao validar cadastro 2FA via HTTP."
                raise RuntimeError(msg)
            chave_registrada = secret
            log.info("2FA registrado via HTTP (validarCadastro2FA) para %s", email)

        elif data.get("solicitarCodigo2FA"):
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

        chave_totp = chave_registrada or (chave_secreta or "").replace(" ", "") or None
        if not chave_totp and gerar_codigo_fn:
            from auto_2fa import obter_chave

            salva = obter_chave(email)
            if salva:
                chave_totp = salva

        out: Dict[str, Any] = {
            "sucesso": True,
            "email": email,
            "session_token": token,
            "phpsessid": phpsessid,
            "bandeiras": bandeiras,
            "mensagem": "Login HTTP no painel concluído.",
        }
        if chave_totp:
            out["chave_totp"] = chave_totp
        return out
    except Exception:
        with _sessions_lock:
            _sessions.pop(token, None)
        raise


def autenticar_acao_2fa(
    http: requests.Session,
    codigo_2fa: Optional[str] = None,
    chave_secreta: Optional[str] = None,
    gerar_codigo_fn=None,
) -> Dict[str, Any]:
    code = codigo_2fa
    if not code and chave_secreta and gerar_codigo_fn:
        code = gerar_codigo_fn(chave_secreta)
    if not code:
        raise RuntimeError("Informe codigo_2fa ou chave_secreta para autenticar ação sensível.")

    r = http.post(
        BASE_URL + "/site/autenticarUsuario2FA",
        data={"code": code},
        headers={"Referer": BASE_URL + "/notificacao/create"},
        timeout=30,
    )
    data = _parse_json_response(r)
    if not data.get("success"):
        msg = data.get("message") or "Falha na autenticação 2FA da ação."
        if data.get("usuarioBloqueado"):
            raise RuntimeError(f"Usuário bloqueado: {msg}")
        raise RuntimeError(msg)
    return {"sucesso": True, "mensagem": "Autenticação 2FA da ação concluída."}


def obter_bandeiras(http: requests.Session) -> List[Dict[str, str]]:
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


def filtrar_destinatarios(
    http: requests.Session,
    form: NotificacaoForm,
) -> Dict[str, Any]:
    if not form.mensagem.strip():
        raise RuntimeError("mensagem é obrigatória.")
    if not form.destinatario:
        raise RuntimeError("destinatario é obrigatório (D ou C).")

    r = _post_create_form(http, form, "filtrar")
    if "notificacao-form" not in r.text and "LoginForm" in r.text:
        raise RuntimeError("Sessão expirada — faça login novamente.")

    report = _extrair_js_json(r.text, "REPORT")
    filtro_id = _extrair_js_json(r.text, "FILTRO")

    if not isinstance(report, dict) or not report.get("report_id"):
        if "Nenhum motorista encontrado" in r.text or "Nenhum resultado encontrado" in r.text:
            return {
                "sucesso": True,
                "report_id": None,
                "filtro_id": filtro_id,
                "total": 0,
                "mensagem": "Nenhum destinatário encontrado com os filtros informados.",
            }
        raise RuntimeError("Falha ao gerar relatório de destinatários.")

    return {
        "sucesso": True,
        "report_id": report.get("report_id"),
        "filtro_id": filtro_id,
        "status_export": report.get("statusExport"),
        "tipo_destinatario": report.get("tipo_destinatario"),
        "mensagem": "Filtro aplicado. Consulte status e total do relatório.",
    }


def verificar_status_relatorio(
    http: requests.Session,
    report_id: int | str,
) -> Dict[str, Any]:
    r = http.get(
        BASE_URL + f"/notificacao/verificarStatusRelatorioDestinatarios/{report_id}",
        timeout=30,
    )
    data = _parse_json_response(r)
    status = data.get("statusExport") or data.get("status")
    pronto = status in STATUS_EXPORT_PRONTO
    pendente = status in STATUS_EXPORT_PENDENTE
    return {
        "sucesso": bool(data.get("success", True)),
        "report_id": report_id,
        "status_export": status,
        "pronto": pronto,
        "pendente": pendente,
        "url_csv": data.get("url"),
        "raw": data,
    }


def obter_total_destinatarios(
    http: requests.Session,
    report_id: int | str,
) -> Dict[str, Any]:
    r = http.get(
        BASE_URL + f"/notificacao/obterTotalDestinatariosReport/{report_id}",
        timeout=30,
    )
    data = _parse_json_response(r)
    return {
        "sucesso": bool(data.get("success")),
        "report_id": report_id,
        "total": data.get("total", 0),
    }


def aguardar_relatorio(
    http: requests.Session,
    report_id: int | str,
    timeout_seg: int = 120,
    intervalo_seg: float = 2.0,
) -> Dict[str, Any]:
    deadline = _now() + timeout_seg
    ultimo: Dict[str, Any] = {}
    while _now() < deadline:
        ultimo = verificar_status_relatorio(http, report_id)
        if ultimo.get("pronto"):
            total = obter_total_destinatarios(http, report_id)
            ultimo["total"] = total.get("total", 0)
            ultimo["mensagem"] = "Relatório pronto."
            return ultimo
        if ultimo.get("status_export") == "canceled":
            raise RuntimeError("Geração do relatório foi cancelada.")
        time.sleep(intervalo_seg)
    raise RuntimeError(
        f"Timeout aguardando relatório {report_id}. Último status: {ultimo.get('status_export')}"
    )


def _resposta_precisa_2fa_acao(response: requests.Response) -> bool:
    """Detecta se o painel pediu 2FA para ação sensível (enviar/agendar).

    O markup `auth-modal` existe no layout de várias páginas (inclusive /index
    após sucesso). Só considerar 2FA quando a resposta ainda está em /create
    e há mensagem explícita de autenticação necessária.
    """
    if "/notificacao/index" in response.url:
        return False
    if "/notificacao/create" not in response.url:
        return False
    lower = (response.text or "").lower()
    return (
        "autenticacao necessária" in lower
        or "autenticacao necessaria" in lower
        or "autenticação necessária" in lower
    )


def enviar_ou_agendar(
    http: requests.Session,
    form: NotificacaoForm,
    agendar: bool = False,
    codigo_2fa_acao: Optional[str] = None,
    chave_secreta: Optional[str] = None,
    gerar_codigo_fn=None,
    tentar_2fa: bool = True,
) -> Dict[str, Any]:
    if not form.mensagem.strip():
        raise RuntimeError("mensagem é obrigatória.")
    if agendar and (not form.data_envio or not form.hora_envio):
        raise RuntimeError("data_envio e hora_envio são obrigatórios para agendamento.")

    r = _post_create_form(http, form, "criar")

    if _resposta_precisa_2fa_acao(r):
        if not tentar_2fa:
            raise RuntimeError("Ação exige autenticação 2FA no painel.")
        autenticar_acao_2fa(
            http,
            codigo_2fa=codigo_2fa_acao,
            chave_secreta=chave_secreta,
            gerar_codigo_fn=gerar_codigo_fn,
        )
        r = _post_create_form(http, form, "criar")

    if "LoginForm" in r.text and "notificacao-form" not in r.text:
        raise RuntimeError("Sessão expirada — faça login novamente.")

    id_notificacao = _extrair_js_json(r.text, "ID_NOTIFICACAO")
    redirect_index = "/notificacao/index" in r.url or "notificacao/index" in r.text[:500]

    erros = re.findall(r'class="errorMessage[^"]*"[^>]*>([^<]+)', r.text)
    if erros:
        raise RuntimeError("; ".join(e.strip() for e in erros if e.strip()))

    sucesso = redirect_index or id_notificacao not in (None, "null", "")
    return {
        "sucesso": sucesso,
        "agendado": agendar,
        "id_notificacao": None if id_notificacao in (None, "null") else id_notificacao,
        "url_final": r.url,
        "mensagem": (
            "Notificação agendada com sucesso."
            if agendar and sucesso
            else "Notificação enviada/agendada."
            if sucesso
            else "Resposta recebida; verifique em /notificacao/index."
        ),
    }


def listar_notificacoes(
    http: requests.Session,
    pagina: int = 1,
) -> Dict[str, Any]:
    params = {"page": pagina} if pagina > 1 else {}
    r = http.get(BASE_URL + "/notificacao/index", params=params, timeout=30)
    if "LoginForm" in r.text and "notificacao_cliente-grid" not in r.text:
        raise RuntimeError("Sessão expirada — faça login novamente.")

    itens: List[Dict[str, str]] = []
    for row in re.finditer(r"<tr class=\"(?:odd|even)\">(.*?)</tr>", r.text, re.S):
        row_html = row.group(1)
        cells = re.findall(
            r'<td class="col col-([^"]+)">([^<]*)</td>',
            row_html,
        )
        if not cells:
            continue
        item = {k: v.strip() for k, v in cells}
        id_match = re.search(r'data-id="(\d+)"', row_html)
        if id_match:
            item["id"] = id_match.group(1)
        itens.append(item)

    return {
        "sucesso": True,
        "pagina": pagina,
        "total_pagina": len(itens),
        "itens": itens,
    }


def cancelar_notificacao(
    http: requests.Session,
    notificacao_id: int | str,
    destinatario: str = "D",
) -> Dict[str, Any]:
    r = http.get(
        BASE_URL + "/notificacao/delete",
        params={"id": str(notificacao_id), "destinatario": destinatario},
        timeout=30,
        allow_redirects=True,
    )
    ok = r.status_code == 200 and "LoginForm" not in r.text
    return {
        "sucesso": ok,
        "id": notificacao_id,
        "mensagem": "Notificação cancelada/excluída." if ok else "Falha ao cancelar.",
    }


def form_from_dict(data: Dict[str, Any]) -> NotificacaoForm:
    categorias = data.get("categorias") or []
    if isinstance(categorias, str):
        categorias = [categorias]
    generos = data.get("generos") or []
    if isinstance(generos, str):
        generos = [generos]
    return NotificacaoForm(
        titulo=data.get("titulo") or "",
        mensagem=data.get("mensagem") or "",
        destinatario=data.get("destinatario") or "D",
        bandeira_id=data.get("bandeira_id"),
        periodo=data.get("periodo") or "hoje",
        quantidade_corridas_operacao=data.get("quantidade_corridas_operacao") or "N",
        quantidade_corridas=str(data.get("quantidade_corridas") or "0"),
        periodo_cadastro=data.get("periodo_cadastro") or "selecione",
        data_inicial=data.get("data_inicial") or "",
        data_final=data.get("data_final") or "",
        data_inicial_cadastro=data.get("data_inicial_cadastro") or "",
        data_final_cadastro=data.get("data_final_cadastro") or "",
        categorias=[str(c) for c in categorias],
        generos=[str(g) for g in generos],
        status_taxi=data.get("status_taxi") or "",
        sistema_operacional_android=str(
            data.get("sistema_operacional_android", "0")
        ),
        sistema_operacional_ios=str(data.get("sistema_operacional_ios", "0")),
        cliente_id=str(data.get("cliente_id") or ""),
        data_envio=data.get("data_envio") or "",
        hora_envio=data.get("hora_envio") or "",
    )


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
