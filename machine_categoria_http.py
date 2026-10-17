"""
Categorias em /categoria/tarifa — listar, ordem e exibição no app passageiro.

Edição via GET/POST /categoria/update/{id} (form personalizar-form).
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from collections import defaultdict
from html import unescape
from typing import Any, Dict, List, Optional, Tuple

import requests

from machine_notificacao_http import BASE_URL
from machine_taxista_http import (
    _autenticar_acao_taxista,
    _limpar_campos_para_post,
    _resposta_precisa_2fa_acao,
)

log = logging.getLogger(__name__)

FIELD_ORDEM = os.environ.get("MACHINE_CATEGORIA_ORDEM_FIELD", "Categoria[ordem_exibicao_app]")
FIELD_EXIBIR = os.environ.get("MACHINE_CATEGORIA_EXIBIR_FIELD", "Categoria[exibir_app]")


class _CategoriaFormParser:
    @staticmethod
    def _form_html(html: str) -> str:
        marker = 'id="personalizar-form"'
        pos = html.find(marker)
        if pos < 0:
            raise RuntimeError("Formulário personalizar-form não encontrado.")
        start = html.rfind("<form", 0, pos + 1)
        end = html.find("</form>", start)
        if end < 0:
            raise RuntimeError("Fim do formulário categoria não encontrado.")
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
            if typ in ("checkbox", "radio"):
                if "checked" in tag.lower():
                    fields[name].append(val if val or typ == "radio" else "on")
            else:
                fields[name].append(val)

        for sm in re.finditer(
            r'<select[^>]+name="([^"]+)"[^>]*>(.*?)</select>', form, re.S | re.I
        ):
            name = sm.group(1)
            chosen: Optional[str] = None
            for om in re.finditer(r"<option([^>]*)>", sm.group(2), re.I):
                attrs = om.group(1)
                if "selected" not in attrs.lower():
                    continue
                val_m = re.search(r'value="([^"]*)"', attrs, re.I)
                chosen = val_m.group(1) if val_m else ""
                break
            if chosen is not None:
                fields[name].append(chosen)
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
            for om in re.finditer(
                r"<option([^>]*)>(.*?)</option>", sm.group(2), re.S | re.I
            ):
                val_m = re.search(r'value="([^"]*)"', om.group(1))
                val = val_m.group(1) if val_m else unescape(om.group(2)).strip()
                text = re.sub(r"<[^>]+>", "", om.group(2))
                text = unescape(text).strip()
                options.append(
                    {
                        "value": val,
                        "text": text,
                        "selected": "selected" in om.group(1).lower(),
                    }
                )
            out.append({"name": name, "options": options})
        return out


def _norm_nome(s: str) -> str:
    t = unicodedata.normalize("NFKD", unescape(s or "").strip())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).casefold()


def listar_categorias_tarifa(http: requests.Session) -> List[Dict[str, Any]]:
    r = http.get(BASE_URL + "/categoria/tarifa", timeout=120)
    if r.status_code == 403:
        raise RuntimeError("HTTP 403 em /categoria/tarifa — sem permissão.")
    text = r.text or ""
    if "LoginForm" in text and "categoria/tarifa" not in (r.url or ""):
        if "duplicarCategoria" not in text:
            raise RuntimeError("Sessão expirada — faça login novamente.")

    ordens = [int(x) for x in re.findall(
        r'target_number[^>]*>\s*<h3>\s*(\d+)\s*</h3>', text
    )]
    pares = re.findall(r"duplicarCategoria\('(\d+)',\s*'([^']*)", text)
    if not pares:
        raise RuntimeError("Nenhuma categoria encontrada em /categoria/tarifa.")

    out: List[Dict[str, Any]] = []
    for i, (cid, nome) in enumerate(pares):
        ordem = ordens[i] if i < len(ordens) else i + 1
        try:
            _, html_up = carregar_form_categoria(http, int(cid))
            exibir_app = _valor_exibir_app(html_up)
        except Exception as exc:
            log.warning(
                "exibir_app categoria %s: fallback tarifa (%s)", cid, exc
            )
            bloco_ini = text.find(f"duplicarCategoria('{cid}'")
            bloco = text[max(0, bloco_ini - 1200) : bloco_ini + 400] if bloco_ini >= 0 else ""
            bloco_u = bloco.upper()
            exibir_app = (
                ("PHONE_IPHONE" in bloco_u or "DISPONÍVEL NO APLICATIVO" in bloco_u
                 or "DISPONIVEL NO APLICATIVO" in bloco_u)
                and "OCULTA" not in bloco_u
                and "NÃO EXIBIDA" not in bloco_u
                and "NAO EXIBIDA" not in bloco_u
            )
        out.append(
            {
                "categoria_id": int(cid),
                "nome": nome.strip(),
                "ordem": ordem,
                "exibir_app": exibir_app,
            }
        )
    return out


def resolver_categoria_por_nome(
    nome: str, categorias: List[Dict[str, Any]]
) -> Dict[str, Any]:
    alvo = _norm_nome(nome)
    if not alvo:
        raise RuntimeError("Informe o nome da categoria.")
    matches = [c for c in categorias if _norm_nome(c.get("nome") or "") == alvo]
    if len(matches) == 1:
        return matches[0]
    parciais = [c for c in categorias if alvo in _norm_nome(c.get("nome") or "")]
    if len(parciais) == 1:
        return parciais[0]
    candidatos = matches or parciais
    if not candidatos:
        nomes = ", ".join(sorted({c["nome"] for c in categorias}))
        raise RuntimeError(f"Categoria '{nome}' não encontrada. Disponíveis: {nomes}")
    lista = ", ".join(f"{c['nome']} (id {c['categoria_id']})" for c in candidatos)
    raise RuntimeError(f"Nome ambíguo '{nome}'. Candidatos: {lista}")


def _set_field(fields: Dict[str, List[str]], name: str, value: str) -> None:
    fields[name] = [value]


def _extrair_icone_atual(html: str) -> str:
    m = re.search(r'const\s+iconeAtual\s*=\s*"([^"]*)"', html)
    if m and (m.group(1) or "").strip():
        return m.group(1).strip()
    return ""


def _valor_dropdown_icone(html: str) -> str:
    parsed = _CategoriaFormParser.parse(html)
    vals = parsed.get("dropdown_icone_app") or []
    if vals and vals[0] not in ("", "0"):
        return vals[0]
    for sel in _CategoriaFormParser.list_selects(html):
        if sel["name"] != "dropdown_icone_app":
            continue
        for opt in sel["options"]:
            if opt.get("selected"):
                return str(opt.get("value") or "")
    return ""


def _sincronizar_icone_para_post(fields: Dict[str, List[str]], html: str) -> None:
    """Painel usa JS para preencher Categoria[icone_app]; o GET vem vazio no hidden."""
    icone = _extrair_icone_atual(html)
    if not icone:
        drop = _valor_dropdown_icone(html)
        if drop and drop != "X":
            icone = drop
    if icone:
        _set_field(fields, "Categoria[icone_app]", icone)
    if icone.startswith("personalizado_"):
        _set_field(fields, "Categoria[eh_icone_personalizado]", "1")
    elif (fields.get("Categoria[eh_icone_personalizado]") or [""])[0] == "":
        _set_field(fields, "Categoria[eh_icone_personalizado]", "0")


def _valor_select(html: str, field_name: str) -> str:
    for sel in _CategoriaFormParser.list_selects(html):
        if sel["name"] != field_name:
            continue
        for opt in sel["options"]:
            if opt.get("selected"):
                return str(opt.get("value") or "")
    parsed = _CategoriaFormParser.parse(html)
    vals = parsed.get(field_name) or []
    return vals[0] if vals else ""


def _exibir_app_na_lista_tarifa(
    http: requests.Session, categoria_id: int
) -> bool:
    for cat in listar_categorias_tarifa(http):
        if int(cat["categoria_id"]) == int(categoria_id):
            return bool(cat.get("exibir_app"))
    raise RuntimeError(f"Categoria {categoria_id} não encontrada em /categoria/tarifa.")


def _validar_exibir_persistido(
    http: requests.Session,
    categoria_id: int,
    nome: str,
    esperado: bool,
) -> None:
    _, html = carregar_form_categoria(http, categoria_id)
    no_form = _valor_exibir_app(html)
    if no_form != esperado:
        raise RuntimeError(
            f"Exibição no app não persistiu no formulário para '{nome}': "
            f"esperado {esperado}, lido {no_form}."
        )


def _valor_exibir_app(html: str) -> bool:
    m = re.search(
        r'name="Categoria\[exibir_app\]"[^>]*value="([^"]*)"', html, re.I
    )
    if m:
        return m.group(1) == "1"
    vals = _CategoriaFormParser.parse(html).get(FIELD_EXIBIR) or []
    return (vals[0] if vals else "0") == "1"


def carregar_form_categoria(
    http: requests.Session, categoria_id: int
) -> Tuple[Dict[str, List[str]], str]:
    url = f"{BASE_URL}/categoria/update/{int(categoria_id)}"
    r = http.get(url, timeout=120)
    text = r.text or ""
    if "personalizar-form" not in text:
        raise RuntimeError(f"Formulário da categoria {categoria_id} não encontrado.")
    fields = _CategoriaFormParser.parse(text)
    return fields, text


def _pairs_from_fields(fields: Dict[str, List[str]]) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    for name, values in fields.items():
        for val in values:
            pairs.append((name, val))
    return pairs


def _validar_erros_post(text: str) -> None:
    erros = re.findall(r'class="errorMessage[^"]*"[^>]*>([^<]+)', text)
    erros += re.findall(r'class="help-block error"[^>]*>([^<]+)', text)
    if erros:
        raise RuntimeError("; ".join(e.strip() for e in erros if e.strip()))
    if "errorSummary" in text:
        m = re.search(
            r'class="errorSummary[^"]*"[^>]*>[\s\S]*?<ul>([\s\S]*?)</ul>', text, re.I
        )
        if m:
            msgs = re.findall(r"<li[^>]*>([^<]+)", m.group(1))
            if msgs:
                raise RuntimeError("; ".join(s.strip() for s in msgs if s.strip()))


def salvar_form_categoria(
    http: requests.Session,
    categoria_id: int,
    fields: Dict[str, List[str]],
    html: str,
    *,
    chave_secreta: Optional[str] = None,
    gerar_codigo_fn: Optional[Any] = None,
) -> None:
    url = f"{BASE_URL}/categoria/update/{int(categoria_id)}"
    _sincronizar_icone_para_post(fields, html)
    fields = _limpar_campos_para_post(fields)
    pairs = _pairs_from_fields(fields)
    headers = {
        "Referer": url,
        "Content-Type": "application/x-www-form-urlencoded",
    }

    if chave_secreta and gerar_codigo_fn:
        _autenticar_acao_taxista(http, url, chave_secreta, gerar_codigo_fn)

    r = http.post(url, data=pairs, headers=headers, timeout=180, allow_redirects=True)
    if _resposta_precisa_2fa_acao(r):
        log.info("Gravar categoria pediu 2FA — autenticando…")
        _autenticar_acao_taxista(http, url, chave_secreta, gerar_codigo_fn)
        r = http.post(url, data=pairs, headers=headers, timeout=180, allow_redirects=True)

    text = r.text or ""
    if "personalizar-form" not in text and r.status_code >= 400:
        raise RuntimeError("Sessão expirada ou falha ao gravar categoria.")
    _validar_erros_post(text)


def _permutar_ordens(
    categorias: List[Dict[str, Any]], nome: str, posicao: int
) -> List[Tuple[int, int]]:
    n = len(categorias)
    if posicao < 1 or posicao > n:
        raise RuntimeError(f"posicao deve estar entre 1 e {n}.")
    ordenadas = sorted(categorias, key=lambda c: int(c["ordem"]))
    alvo = resolver_categoria_por_nome(nome, categorias)
    resto = [c for c in ordenadas if c["categoria_id"] != alvo["categoria_id"]]
    idx = max(0, min(posicao - 1, len(resto)))
    nova_lista = resto[:idx] + [alvo] + resto[idx:]
    mudancas: List[Tuple[int, int]] = []
    for i, cat in enumerate(nova_lista, start=1):
        if int(cat["ordem"]) != i:
            mudancas.append((int(cat["categoria_id"]), i))
    return mudancas


def _aplicar_exibir_no_form(
    fields: Dict[str, List[str]], exibir: bool
) -> None:
    _set_field(fields, FIELD_EXIBIR, "1" if exibir else "0")


def definir_ordem_por_nome(
    http: requests.Session,
    nome: str,
    posicao: int,
    *,
    exibir: Optional[bool] = None,
    chave_secreta: Optional[str] = None,
    gerar_codigo_fn: Optional[Any] = None,
) -> Dict[str, Any]:
    categorias = listar_categorias_tarifa(http)
    mudancas = _permutar_ordens(categorias, nome, posicao)
    alvo = resolver_categoria_por_nome(nome, categorias)
    alvo_id = int(alvo["categoria_id"])
    aplicadas: List[Dict[str, Any]] = []

    if not mudancas and exibir is None:
        return {
            "categoria_id": alvo_id,
            "nome": alvo["nome"],
            "ordem": int(alvo["ordem"]),
            "alteradas": 0,
        }

    alvo_no_lote = any(cid == alvo_id for cid, _ in mudancas)

    for cid, nova_ordem in mudancas:
        fields, html = carregar_form_categoria(http, cid)
        antes = (fields.get(FIELD_ORDEM) or [""])[0]
        _set_field(fields, FIELD_ORDEM, str(nova_ordem))
        if exibir is not None and cid == alvo_id:
            _aplicar_exibir_no_form(fields, exibir)
        salvar_form_categoria(
            http,
            cid,
            fields,
            html,
            chave_secreta=chave_secreta,
            gerar_codigo_fn=gerar_codigo_fn,
        )
        aplicadas.append(
            {"categoria_id": cid, "ordem_anterior": antes, "ordem_aplicada": nova_ordem}
        )

    if exibir is not None and not alvo_no_lote:
        fields, html = carregar_form_categoria(http, alvo_id)
        _aplicar_exibir_no_form(fields, exibir)
        salvar_form_categoria(
            http,
            alvo_id,
            fields,
            html,
            chave_secreta=chave_secreta,
            gerar_codigo_fn=gerar_codigo_fn,
        )
        aplicadas.append({"categoria_id": alvo_id, "exibir_aplicado": exibir})

    depois = listar_categorias_tarifa(http)
    cat = resolver_categoria_por_nome(nome, depois)
    if int(cat["ordem"]) != posicao:
        raise RuntimeError(
            f"Ordem não persistiu para '{nome}': esperado {posicao}, lido {cat['ordem']}."
        )
    if exibir is not None:
        _validar_exibir_persistido(http, alvo_id, nome, exibir)

    out: Dict[str, Any] = {
        "categoria_id": cat["categoria_id"],
        "nome": cat["nome"],
        "ordem": int(cat["ordem"]),
        "alteradas": len(aplicadas),
        "detalhes": aplicadas,
    }
    if exibir is not None:
        out["exibir_app"] = exibir
    return out


def definir_exibicao_app_por_nome(
    http: requests.Session,
    nome: str,
    exibir: bool,
    *,
    chave_secreta: Optional[str] = None,
    gerar_codigo_fn: Optional[Any] = None,
) -> Dict[str, Any]:
    categorias = listar_categorias_tarifa(http)
    cat = resolver_categoria_por_nome(nome, categorias)
    cid = int(cat["categoria_id"])
    fields, html = carregar_form_categoria(http, cid)
    antes = _valor_exibir_app(html)
    valor = "1" if exibir else "0"
    _set_field(fields, FIELD_EXIBIR, valor)
    salvar_form_categoria(
        http,
        cid,
        fields,
        html,
        chave_secreta=chave_secreta,
        gerar_codigo_fn=gerar_codigo_fn,
    )

    _validar_exibir_persistido(http, cid, cat["nome"], exibir)
    return {
        "categoria_id": cid,
        "nome": cat["nome"],
        "exibir_app": exibir,
        "valor_anterior": antes,
    }
