"""
Alteração do campo Relação / tipo de vínculo em /taxista/update/{id} (painel Yii).

Reutiliza sessão de machine_notificacao_http (login_painel / get_session).
"""

from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from html import unescape
from typing import Any, Dict, List, Literal, Optional, Tuple

import requests

from machine_notificacao_http import BASE_URL, autenticar_acao_2fa

log = logging.getLogger(__name__)

RelacaoTaxista = Literal["prioritario", "secundario"]

_RELACAO_ALIASES = {
    "prioritario": ("prioritario", "prioritário", "motorista prioritario", "motorista prioritário"),
    "secundario": ("secundario", "secundário", "motorista secundario", "motorista secundário"),
}

_NAME_HINTS = (
    "relacao",
    "relação",
    "vinculo",
    "vínculo",
    "taxista_apoio",
    "apoio",
    "tipo_vinculo",
    "tipo_vinculo_taxista",
)


class _TaxistaFormParser:
    """Extrai campos do formulário de edição do taxista (regex; ignora script)."""

    @staticmethod
    def _form_html(html: str) -> str:
        markers = ('id="taxista-form"', 'name="Taxista[', "taxista-form")
        start = -1
        for marker in markers:
            pos = html.find(marker)
            if pos < 0:
                continue
            cand = html.rfind("<form", 0, pos + 1)
            if cand >= 0:
                start = cand
                break
        if start < 0:
            m = re.search(r"<form[^>]+action=[^>]*taxista/update", html, re.I)
            if m:
                start = m.start()
        if start < 0:
            raise RuntimeError("Formulário taxista/update não encontrado na página.")
        end = html.find("</form>", start)
        if end < 0:
            raise RuntimeError("Fim do formulário taxista não encontrado.")
        form = html[start : end + len("</form>")]
        return re.sub(r"<script[\s\S]*?</script>", "", form, flags=re.I)

    @classmethod
    def parse(cls, html: str) -> Dict[str, List[str]]:
        form = cls._form_html(html)
        fields: Dict[str, List[str]] = defaultdict(list)

        for tag in re.findall(r"<input[^>]+>", form, flags=re.I):
            name_m = re.search(r'name="([^"]+)"', tag)
            if not name_m:
                continue
            name = name_m.group(1)
            typ_m = re.search(r'type="([^"]+)"', tag, re.I)
            typ = (typ_m.group(1) if typ_m else "text").lower()
            if typ in ("submit", "button", "file", "image"):
                continue
            val_m = re.search(r'value="([^"]*)"', tag)
            val = val_m.group(1) if val_m else ""
            checked = "checked" in tag.lower()
            if typ in ("checkbox", "radio"):
                if checked:
                    fields[name].append(val if val or typ == "radio" else "on")
            else:
                fields[name].append(val)

        for sm in re.finditer(
            r'<select[^>]+name="([^"]+)"[^>]*>(.*?)</select>', form, re.S | re.I
        ):
            name = sm.group(1)
            selected = re.findall(
                r'<option[^>]*selected[^>]*value="([^"]*)"', sm.group(2), re.I
            )
            if selected:
                fields[name].extend(selected)
            else:
                first = re.search(r'<option[^>]*value="([^"]*)"', sm.group(2), re.I)
                if first:
                    fields[name].append(first.group(1))

        for tm in re.finditer(
            r'<textarea[^>]+name="([^"]+)"[^>]*>(.*?)</textarea>', form, re.S | re.I
        ):
            fields[tm.group(1)].append(tm.group(2))

        return dict(fields)

    @classmethod
    def list_selects(cls, html: str) -> List[Dict[str, Any]]:
        form = cls._form_html(html)
        out: List[Dict[str, Any]] = []
        for sm in re.finditer(
            r'<select[^>]+name="([^"]+)"[^>]*>(.*?)</select>', form, re.S | re.I
        ):
            name = sm.group(1)
            options: List[Dict[str, str]] = []
            for om in re.finditer(r"<option([^>]*)>(.*?)</option>", sm.group(2), re.S | re.I):
                val_m = re.search(r'value="([^"]*)"', om.group(1))
                val = val_m.group(1) if val_m else unescape(om.group(2)).strip()
                text = re.sub(r"<[^>]+>", "", om.group(2))
                text = unescape(text).strip()
                options.append({"value": val, "text": text, "selected": "selected" in om.group(1).lower()})
            out.append({"name": name, "options": options})
        return out


def _norm_label(s: str) -> str:
    return (
        unescape(s or "")
        .strip()
        .lower()
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("ã", "a")
        .replace("õ", "o")
        .replace("ç", "c")
    )


def _option_matches_relacao(option_text: str, relacao: RelacaoTaxista) -> bool:
    t = _norm_label(option_text)
    for alias in _RELACAO_ALIASES[relacao]:
        if _norm_label(alias) in t or t in _norm_label(alias):
            return True
    if relacao == "prioritario" and "priorit" in t and "secund" not in t:
        return True
    if relacao == "secundario" and "secund" in t:
        return True
    return False


def _field_name_matches_relacao(field_name: str) -> bool:
    n = _norm_label(field_name)
    return any(h in n for h in _NAME_HINTS)


def descobrir_campo_relacao(html: str) -> Tuple[str, Dict[str, str]]:
    """
    Retorna (field_name, mapa relacao -> option value).
    Permite override via env MACHINE_TAXISTA_RELACAO_FIELD e
    MACHINE_TAXISTA_RELACAO_PRIORITARIO_VALUE / SECUNDARIO_VALUE.
    """
    forced = (os.environ.get("MACHINE_TAXISTA_RELACAO_FIELD") or "").strip()
    pri_val = (os.environ.get("MACHINE_TAXISTA_RELACAO_PRIORITARIO_VALUE") or "").strip()
    sec_val = (os.environ.get("MACHINE_TAXISTA_RELACAO_SECUNDARIO_VALUE") or "").strip()
    if forced and pri_val and sec_val:
        return forced, {"prioritario": pri_val, "secundario": sec_val}

    selects = _TaxistaFormParser.list_selects(html)
    best: Optional[Tuple[str, Dict[str, str], int]] = None
    for sel in selects:
        name = sel["name"]
        if not name.startswith("Taxista"):
            continue
        mapping: Dict[str, str] = {}
        score = 0
        if _field_name_matches_relacao(name):
            score += 10
        for opt in sel["options"]:
            text = opt.get("text") or ""
            val = opt.get("value") or ""
            if not val and not text:
                continue
            if _option_matches_relacao(text, "prioritario"):
                mapping["prioritario"] = val
                score += 5
            if _option_matches_relacao(text, "secundario"):
                mapping["secundario"] = val
                score += 5
        if "prioritario" in mapping and "secundario" in mapping:
            if best is None or score > best[2]:
                best = (name, mapping, score)

    if not best:
        raise RuntimeError(
            "Campo Relação não encontrado no HTML de /taxista/update. "
            "Rode scripts/probe_taxista_update_relacao.py ou defina "
            "MACHINE_TAXISTA_RELACAO_FIELD e valores *_PRIORITARIO_* / *_SECUNDARIO_*."
        )
    return best[0], best[1]


def _resposta_precisa_2fa_acao(response: requests.Response) -> bool:
    lower = (response.text or "").lower()
    return (
        "autenticacao necessária" in lower
        or "autenticacao necessaria" in lower
        or "autenticação necessária" in lower
        or ("solicitar2fa" in lower and "auth-modal" in lower)
    )


def _set_field(fields: Dict[str, List[str]], name: str, value: str) -> None:
    fields[name] = [value]


def _pairs_from_fields(fields: Dict[str, List[str]]) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    for name, values in fields.items():
        for val in values:
            pairs.append((name, val))
    if not any(n.startswith("yt") for n in fields):
        pairs.append(("yt0", "Salvar"))
    return pairs


def carregar_form_taxista(http: requests.Session, taxista_id: int) -> Tuple[Dict[str, List[str]], str]:
    url = f"{BASE_URL}/taxista/update/{int(taxista_id)}"
    r = http.get(url, timeout=120)
    if r.status_code == 403:
        raise RuntimeError("HTTP 403 em /taxista/update — conta sem permissão.")
    text = r.text or ""
    if "LoginForm" in text and "Taxista[" not in text:
        raise RuntimeError("Sessão expirada — faça login novamente.")
    if "Taxista[" not in text:
        raise RuntimeError(f"Página do taxista {taxista_id} não encontrada ou sem formulário.")
    fields = _TaxistaFormParser.parse(text)
    return fields, text


def atualizar_relacao_taxista(
    http: requests.Session,
    taxista_id: int,
    relacao: RelacaoTaxista,
    *,
    chave_secreta: Optional[str] = None,
    gerar_codigo_fn: Optional[Any] = None,
) -> Dict[str, Any]:
    fields, html = carregar_form_taxista(http, taxista_id)
    field_name, value_map = descobrir_campo_relacao(html)
    if relacao not in value_map:
        raise RuntimeError(f"Valor de relação '{relacao}' não mapeado para o campo {field_name}.")
    antes = (fields.get(field_name) or [""])[0]
    novo = value_map[relacao]
    _set_field(fields, field_name, novo)

    url = f"{BASE_URL}/taxista/update/{int(taxista_id)}"
    pairs = _pairs_from_fields(fields)
    headers = {"Referer": url}

    r = http.post(url, data=pairs, headers=headers, timeout=180, allow_redirects=True)
    if _resposta_precisa_2fa_acao(r):
        log.info("Gravar taxista pediu 2FA de ação — autenticando…")
        autenticar_acao_2fa(http, chave_secreta=chave_secreta, gerar_codigo_fn=gerar_codigo_fn)
        r = http.post(url, data=pairs, headers=headers, timeout=180, allow_redirects=True)

    text = r.text or ""
    if "LoginForm" in text and "Taxista[" not in text:
        raise RuntimeError("Sessão expirada ao gravar taxista.")

    erros = re.findall(r'class="errorMessage[^"]*"[^>]*>([^<]+)', text)
    if erros:
        raise RuntimeError("; ".join(e.strip() for e in erros if e.strip()))

    if "errorSummary" in text:
        m = re.search(r'class="errorSummary[^"]*"[^>]*>[\s\S]*?<ul>([\s\S]*?)</ul>', text, re.I)
        if m:
            msgs = re.findall(r"<li[^>]*>([^<]+)", m.group(1))
            if msgs:
                raise RuntimeError("; ".join(s.strip() for s in msgs if s.strip()))

    log.info(
        "taxista %s relação %s: campo=%s %s -> %s",
        taxista_id,
        relacao,
        field_name,
        antes,
        novo,
    )
    return {
        "taxista_id": int(taxista_id),
        "relacao": relacao,
        "campo": field_name,
        "valor_anterior": antes,
        "valor_aplicado": novo,
    }
