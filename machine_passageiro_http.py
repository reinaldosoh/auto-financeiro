"""
Ficha do passageiro no painel TaxiMachine.

GET /cliente/cadastroPassageiro/{id} com a mesma sessão (PHPSESSID)
usada por /notificacao e /dashboard-v2.
"""

from __future__ import annotations

import logging
import re
from html import unescape
from typing import Any, Dict, Optional

import requests

from machine_notificacao_http import BASE_URL

log = logging.getLogger(__name__)

_INPUT_RE = re.compile(
    r"<input\b([^>]*)>",
    re.IGNORECASE,
)
_SELECT_RE = re.compile(
    r"<select\b([^>]*)>(.*?)</select>",
    re.IGNORECASE | re.DOTALL,
)
_OPTION_RE = re.compile(
    r"<option\b([^>]*)>(.*?)</option>",
    re.IGNORECASE | re.DOTALL,
)
_ATTR_RE = re.compile(r"""([a-zA-Z_:][\w:.-]*)\s*=\s*(['"])(.*?)\2""", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _attrs(blob: str) -> Dict[str, str]:
    return {m.group(1).lower(): unescape(m.group(3)) for m in _ATTR_RE.finditer(blob)}


def _strip_tags(html: str) -> str:
    return unescape(_TAG_RE.sub(" ", html)).replace("\xa0", " ")


def _norm(s: str) -> str:
    return (
        unescape(s or "")
        .strip()
        .lower()
        .encode("ascii", "ignore")
        .decode("ascii")
    )


def _somente_digitos(s: Optional[str]) -> str:
    return re.sub(r"\D+", "", s or "")


def _parse_inputs(html: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for m in _INPUT_RE.finditer(html):
        a = _attrs(m.group(1))
        name = a.get("name") or a.get("id") or ""
        if not name:
            continue
        typ = (a.get("type") or "text").lower()
        if typ in {"checkbox", "radio"} and "checked" not in m.group(1).lower():
            continue
        out[name] = a.get("value") or ""
    return out


def _parse_selects(html: str) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for m in _SELECT_RE.finditer(html):
        a = _attrs(m.group(1))
        name = a.get("name") or a.get("id") or ""
        if not name:
            continue
        escolhido: Dict[str, str] = {"value": "", "text": ""}
        for om in _OPTION_RE.finditer(m.group(2)):
            oa = _attrs(om.group(1))
            text = _strip_tags(om.group(2)).strip()
            val = oa.get("value", text)
            if "selected" in om.group(1).lower() or (not escolhido["value"] and val):
                escolhido = {"value": val, "text": text}
        out[name] = escolhido
    return out


def _campo_por_chaves(mapa: Dict[str, str], *chaves: str) -> Optional[str]:
    alvo = [_norm(c) for c in chaves]
    for name, val in mapa.items():
        n = _norm(name)
        if any(c and c in n for c in alvo):
            v = (val or "").strip()
            if v:
                return v
    return None


def _select_por_chaves(
    selects: Dict[str, Dict[str, str]], *chaves: str
) -> Optional[str]:
    alvo = [_norm(c) for c in chaves]
    for name, item in selects.items():
        n = _norm(name)
        if any(c and c in n for c in alvo):
            text = (item.get("text") or "").strip()
            val = (item.get("value") or "").strip()
            if text and _norm(text) not in {"selecione", "selecionar", "-"}:
                return text
            if val:
                return val
    return None


def _card(html: str, *rotulos: str) -> Optional[str]:
    texto = _strip_tags(html)
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n{2,}", "\n", texto)
    for rotulo in rotulos:
        padrao = re.compile(
            rf"{re.escape(rotulo)}\s*[:\?]?\s*([^\n<]{{1,80}})",
            re.IGNORECASE,
        )
        m = padrao.search(texto)
        if m:
            val = m.group(1).strip(" :\t")
            if val:
                return val
    return None


def _split_versao_so(raw: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not raw:
        return None, None
    s = raw.strip()
    m = re.match(r"^([\d.]+)\s*(.+)?$", s)
    if m:
        ver = m.group(1)
        so_raw = (m.group(2) or "").strip()
    else:
        ver, so_raw = s, ""
    so = None
    blob = _norm(so_raw or s)
    if "ios" in blob or "iphone" in blob or "ipad" in blob:
        so = "ios"
    elif "android" in blob:
        so = "android"
    return ver or None, so


def _genero(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    n = _norm(raw)
    if n in {"m", "1"} or "mascul" in n:
        return "Masculino"
    if n in {"f", "2"} or "femin" in n:
        return "Feminino"
    if "outro" in n or n in {"o", "3"}:
        return "Outro"
    return raw.strip() or None


def obter_ficha_passageiro(http: requests.Session, id_machine: str) -> Dict[str, Any]:
    mid = str(id_machine).strip()
    if not mid.isdigit():
        raise RuntimeError("id_machine inválido")

    url = f"{BASE_URL}/cliente/cadastroPassageiro/{mid}"
    r = http.get(url, timeout=45, headers={"Accept": "text/html,application/xhtml+xml"})
    html = r.text or ""
    if r.status_code != 200 or "LoginForm" in html[:4000]:
        raise RuntimeError("Sessão inválida ou expirada — faça login novamente.")
    if "cadastroPassageiro" not in r.url and "cadastroPassageiro" not in html[:8000]:
        if "não encontrado" in html.lower() or "nao encontrado" in html.lower():
            raise RuntimeError(f"Passageiro {mid} não encontrado no painel.")

    inputs = _parse_inputs(html)
    selects = _parse_selects(html)

    nome = _campo_por_chaves(inputs, "[nome]", "nome")
    email = _campo_por_chaves(inputs, "[email]", "e-mail", "email")
    telefone = (
        _campo_por_chaves(inputs, "telefone_internacional", "telefoneinternacional")
        or _campo_por_chaves(inputs, "[telefone]", "telefone")
    )
    cpf = _campo_por_chaves(inputs, "[cpf]", "cpf")
    genero = _genero(
        _select_por_chaves(selects, "genero", "sexo", "gender")
        or _campo_por_chaves(inputs, "genero", "sexo")
    )
    status = _select_por_chaves(selects, "[status]", "status") or _campo_por_chaves(
        inputs, "[status]", "status"
    )
    tipo = _select_por_chaves(selects, "[tipo]", "tipo") or _campo_por_chaves(
        inputs, "[tipo]", "tipo"
    )

    qtd_raw = _card(html, "Quantidade de corridas", "Qtd. corridas", "Qtd corridas")
    dias_raw = _card(html, "Dias sem realizar login", "Dias sem login")
    ult_est = _card(html, "Última estimativa realizada", "Ultima estimativa realizada", "Última estimativa")
    ult_cor = _card(html, "Última corrida realizada", "Ultima corrida realizada", "Última corrida")
    cad_em = _card(html, "Cadastrado em", "Ativo desde")
    versao_raw = _card(html, "Versão do aplicativo", "Versao do aplicativo")
    versao_app, so = _split_versao_so(versao_raw)

    qtd = None
    if qtd_raw:
        digits = _somente_digitos(qtd_raw)
        if digits:
            qtd = int(digits)
    dias = None
    if dias_raw:
        digits = _somente_digitos(dias_raw)
        if digits:
            dias = int(digits)

    ficha = {
        "id_machine": mid,
        "nome": nome,
        "email": email,
        "telefone": telefone,
        "cpf": cpf,
        "genero": genero,
        "status": status,
        "tipo": tipo,
        "registrado_em": cad_em,
        "qtd_corridas": qtd,
        "dias_sem_login": dias,
        "ultima_estimativa": ult_est,
        "ultima_corrida": ult_cor,
        "sistema_operacional": so,
        "versao_app": versao_app,
    }
    log.info("ficha passageiro %s qtd=%s so=%s", mid, qtd, so)
    return ficha
