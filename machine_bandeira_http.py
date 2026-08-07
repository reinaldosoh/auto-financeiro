"""
Cliente HTTP para banners em /bandeira/update (Recursos Premium).

Substitui Selenium nos fluxos motorista, passageiro e campanha no ciclo da corrida.
Reutiliza login e sessão de machine_notificacao_http.
"""

from __future__ import annotations

import io
import json
import logging
import re
from collections import defaultdict
from datetime import date, timedelta
from html.parser import HTMLParser
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from machine_notificacao_http import (
    BASE_URL,
    _extrair_js_json,
    _parse_json_response,
    get_session,
    login_painel,
    obter_bandeiras,
)

log = logging.getLogger(__name__)

REFERER_BANDEIRA = BASE_URL + "/bandeira/update"

# Variáveis JS do painel (anuncio.js / campanha.js)
JS_LISTA_PASSAGEIRO = "listaAnunciosTelaInicialAppPassageiro"
JS_LISTA_MOTORISTA = "listaAnunciosTelaInicialAppTaxista"
JS_LISTA_CAMPANHA = "listaCampanhas"


class _BandeiraFormParser(HTMLParser):
    """Extrai campos do formulário #bandeira-form."""

    def __init__(self) -> None:
        super().__init__()
        self.in_form = False
        self.depth = 0
        self.fields: Dict[str, List[str]] = defaultdict(list)
        self._select_name: Optional[str] = None
        self._textarea_name: Optional[str] = None

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        ad = {k: (v or "") for k, v in attrs}
        if tag == "form" and ad.get("id") == "bandeira-form":
            self.in_form = True
            self.depth = 1
            return
        if not self.in_form:
            return
        if tag == "form":
            self.depth += 1
        elif tag == "input":
            name = ad.get("name")
            if not name:
                return
            typ = ad.get("type", "text").lower()
            if typ in ("submit", "button", "file", "image"):
                return
            if typ in ("checkbox", "radio"):
                if "checked" in ad:
                    self.fields[name].append(ad.get("value", "1"))
            else:
                self.fields[name].append(ad.get("value", ""))
        elif tag == "select":
            self._select_name = ad.get("name")
        elif tag == "option" and self._select_name:
            if "selected" in ad:
                self.fields[self._select_name].append(ad.get("value", ""))
        elif tag == "textarea":
            self._textarea_name = ad.get("name")

    def handle_endtag(self, tag: str) -> None:
        if not self.in_form:
            return
        if tag == "select":
            self._select_name = None
        elif tag == "textarea":
            self._textarea_name = None
        elif tag == "form":
            self.depth -= 1
            if self.depth <= 0:
                self.in_form = False

    def handle_data(self, data: str) -> None:
        if self._textarea_name:
            self.fields[self._textarea_name].append(data)


def extrair_bandeira_id(html: str) -> str:
    for pat in (
        r"bandeiraId\s*[:=]\s*['\"]?(\d+)",
        r"var\s+bandeiraId\s*=\s*(\d+)",
        r'"bandeiraId"\s*:\s*"?(\d+)"?',
    ):
        m = re.search(pat, html)
        if m:
            return m.group(1)
    raise RuntimeError("bandeiraId não encontrado em /bandeira/update.")


def carregar_form_bandeira(http: requests.Session) -> Tuple[str, Dict[str, List[str]], str]:
    r = http.get(BASE_URL + "/bandeira/update", timeout=120)
    if r.status_code == 403:
        raise RuntimeError("HTTP 403 em /bandeira/update — conta sem permissão de bandeira.")
    if "LoginForm" in r.text and "bandeira-form" not in r.text:
        raise RuntimeError("Sessão expirada — faça login novamente.")
    if "bandeira-form" not in r.text:
        raise RuntimeError("Formulário bandeira-form não encontrado.")

    bandeira_id = extrair_bandeira_id(r.text)
    parser = _BandeiraFormParser()
    parser.feed(r.text)
    return bandeira_id, dict(parser.fields), r.text


def _set_field(fields: Dict[str, List[str]], name: str, value: str) -> None:
    fields[name] = [value]


def _extrair_lista_js(html: str, var_name: str) -> List[Dict[str, Any]]:
    data = _extrair_js_json(html, var_name)
    if isinstance(data, list):
        return data
    m = re.search(rf"{re.escape(var_name)}\s*=\s*(\[[\s\S]*?\])\s*;", html)
    if not m:
        return []
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return []


def _max_anuncios_passageiro(html: str) -> int:
    """Limite real do painel (MAP_ANUNCIO_TIPO_MAX); passageiro = 70 na UBIZ CAR."""
    m = re.search(r"TIPO_TELA_INICIAL_APP_PASSAGEIRO\]\s*:\s*(\d+)", html)
    if m:
        return int(m.group(1))
    return 70


def _ids_bandeiras(
    html: str,
    http: requests.Session,
    selecionar_todas: bool,
    bandeira_ids: Optional[List[str]] = None,
) -> List[str]:
    if bandeira_ids:
        return [str(b) for b in bandeira_ids if str(b).strip()]
    if not selecionar_todas:
        return []
    m = re.search(r"window\.listaBandeiras\s*=\s*(\{[^;]+\})", html)
    if m:
        try:
            return list(json.loads(m.group(1)).keys())
        except json.JSONDecodeError:
            pass
    return [b["id"] for b in obter_bandeiras(http) if b.get("id")]


def _autenticar_acao_bandeira(
    http: requests.Session,
    chave_secreta: Optional[str],
    gerar_codigo_fn: Optional[Callable[[str], str]],
) -> None:
    code = None
    if chave_secreta and gerar_codigo_fn:
        code = gerar_codigo_fn(chave_secreta.replace(" ", ""))
    if not code:
        raise RuntimeError("2FA de ação exigido ao gravar banner — informe chave_secreta válida.")

    r = http.post(
        BASE_URL + "/site/autenticarUsuario2FA",
        data={"code": code},
        headers={"Referer": REFERER_BANDEIRA},
        timeout=30,
    )
    data = _parse_json_response(r)
    if not data.get("success"):
        msg = data.get("message") or "Falha na autenticação 2FA da ação (bandeira)."
        raise RuntimeError(msg)


def _resposta_precisa_2fa_acao(response: requests.Response) -> bool:
    lower = (response.text or "").lower()
    return (
        "autenticacao necessária" in lower
        or "autenticacao necessaria" in lower
        or "autenticação necessária" in lower
        or "solicitar2fa" in lower and "auth-modal" in lower
    )


def salvar_bandeira(
    http: requests.Session,
    fields: Dict[str, List[str]],
    chave_secreta: Optional[str] = None,
    gerar_codigo_fn: Optional[Callable[[str], str]] = None,
) -> str:
    """POST /bandeira/update. Retorna HTML da resposta para verificação."""
    pairs: List[Tuple[str, str]] = []
    for name, values in fields.items():
        for val in values:
            pairs.append((name, val))

    # Botão real do painel: name=yt1 (não yt0)
    if not any(n.startswith("yt") for n in fields):
        pairs.append(("yt1", "Gravar"))

    r = http.post(
        BASE_URL + "/bandeira/update",
        data=pairs,
        headers={"Referer": REFERER_BANDEIRA},
        timeout=180,
        allow_redirects=True,
    )

    if _resposta_precisa_2fa_acao(r):
        log.info("Gravar bandeira pediu 2FA de ação — autenticando…")
        _autenticar_acao_bandeira(http, chave_secreta, gerar_codigo_fn)
        r = http.post(
            BASE_URL + "/bandeira/update",
            data=pairs,
            headers={"Referer": REFERER_BANDEIRA},
            timeout=180,
            allow_redirects=True,
        )

    if "LoginForm" in r.text and "bandeira-form" not in r.text:
        raise RuntimeError("Sessão expirada ao gravar bandeira.")

    erros = re.findall(r'class="errorMessage[^"]*"[^>]*>([^<]+)', r.text or "")
    if erros:
        raise RuntimeError("; ".join(e.strip() for e in erros if e.strip()))

    return r.text or ""


def upload_imagem_configuracao(
    http: requests.Session,
    bandeira_id: str,
    img_bytes: bytes,
    tipo: str,
    campo: str,
    filename: str = "banner.jpeg",
) -> str:
    files = {"foto": (filename, io.BytesIO(img_bytes), "image/jpeg")}
    data = {"id": str(bandeira_id), "tipo": tipo, "campo": campo}
    r = http.post(
        BASE_URL + "/bandeira/salvarImagemConfiguracao",
        data=data,
        files=files,
        headers={"Referer": REFERER_BANDEIRA, "X-Requested-With": "XMLHttpRequest"},
        timeout=120,
    )
    body = _parse_json_response(r)
    if not body.get("success"):
        err = body.get("errors")
        if isinstance(err, list) and err:
            raise RuntimeError(str(err[0]))
        raise RuntimeError(f"Upload falhou: {body}")
    url_s3 = body.get("urlS3") or ""
    if not url_s3:
        raise RuntimeError("Upload não retornou urlS3.")
    return url_s3


def _ativar_exibir(fields: Dict[str, List[str]], nome_campo: str) -> None:
    """Sim = valor '1' no painel (id *_0)."""
    _set_field(fields, nome_campo, "1")


def _preencher_slot_anuncio(
    fields: Dict[str, List[str]],
    nome_mod: str,
    idx: int,
    url_imagem: str,
    link: str,
    bandeira_ids: List[str],
    item_id: str = "",
    excluido: str = "0",
) -> None:
    base = f"{nome_mod}[lista][{idx}]"
    _set_field(fields, f"{base}[url_imagem]", url_imagem)
    _set_field(fields, f"{base}[url_anuncio]", link)
    _set_field(fields, f"{base}[excluido]", excluido)
    _set_field(fields, f"{base}[ativo]", "1")
    _set_field(fields, f"{base}[id]", item_id)
    if bandeira_ids:
        fields[f"{base}[bandeiras][]"] = list(bandeira_ids)


def _mesclar_lista_anuncios_no_form(
    fields: Dict[str, List[str]],
    nome_mod: str,
    lista: List[Dict[str, Any]],
    idx_editar: int,
) -> None:
    """Preserva anúncios existentes no POST ao editar/adicionar um slot."""
    for idx, item in enumerate(lista):
        if idx == idx_editar:
            continue
        base = f"{nome_mod}[lista][{idx}]"
        if str(item.get("excluido", "0")) == "1":
            _set_field(fields, f"{base}[excluido]", "1")
            if item.get("id"):
                _set_field(fields, f"{base}[id]", str(item["id"]))
            continue
        _set_field(fields, f"{base}[url_imagem]", (item.get("url_imagem") or "").strip())
        _set_field(fields, f"{base}[url_anuncio]", (item.get("url_anuncio") or "").strip())
        _set_field(fields, f"{base}[excluido]", "0")
        _set_field(fields, f"{base}[ativo]", "1")
        if item.get("id"):
            _set_field(fields, f"{base}[id]", str(item["id"]))
        bs = item.get("bandeiras") or []
        if bs:
            fields[f"{base}[bandeiras][]"] = [str(b) for b in bs]


def _escolher_slot_passageiro(
    lista: List[Dict[str, Any]],
    html: str,
    bandeira_ids: Optional[List[str]] = None,
) -> Tuple[Optional[int], Optional[str]]:
    """
    Escolhe índice para gravar anúncio passageiro.
    Prioridade: slot da bandeira alvo → slot vazio → novo índice (até max do painel).
    """
    alvo = {str(b) for b in (bandeira_ids or []) if str(b).strip()}

    if alvo:
        for idx, item in enumerate(lista):
            if str(item.get("excluido", "0")) == "1":
                continue
            bs = {str(b) for b in (item.get("bandeiras") or [])}
            if bs & alvo:
                return idx, "bandeira"

    for idx, item in enumerate(lista):
        if str(item.get("excluido", "0")) == "1":
            return idx, "excluido"
        if not (item.get("url_imagem") or item.get("url_anuncio")):
            return idx, "vazio"

    maximo = _max_anuncios_passageiro(html)
    ativos = [
        a for a in lista
        if str(a.get("excluido", "0")) != "1" and (a.get("url_imagem") or a.get("url_anuncio"))
    ]
    if len(ativos) >= maximo:
        return None, f"Limite de {maximo} anúncios passageiros no painel."
    return len(lista), "novo"


def _preencher_slot_campanha(
    fields: Dict[str, List[str]],
    idx: int,
    url_imagem: str,
    link: str,
    limite: str,
    data_ini: str,
    data_fim: str,
    bandeira_ids: List[str],
    item_id: str = "",
    excluido: str = "0",
) -> None:
    base = f"Campanha[lista][{idx}]"
    _set_field(fields, f"{base}[url_imagem]", url_imagem)
    _set_field(fields, f"{base}[url_campanha]", link)
    _set_field(fields, f"{base}[excluido]", excluido)
    _set_field(fields, f"{base}[ativo]", "1")
    _set_field(fields, f"{base}[id]", item_id)
    _set_field(fields, f"{base}[limite_solicitacoes_finalizadas]", limite)
    _set_field(fields, f"{base}[data_hora_inicio]", data_ini)
    _set_field(fields, f"{base}[data_hora_fim]", data_fim)
    if bandeira_ids:
        fields[f"{base}[bandeiras][]"] = list(bandeira_ids)


def _marcar_excluidos_lista(
    fields: Dict[str, List[str]],
    nome_mod: str,
    lista: List[Dict[str, Any]],
) -> None:
    for idx, item in enumerate(lista):
        if str(item.get("excluido", "0")) == "1":
            continue
        if item.get("url_imagem") or item.get("url_anuncio"):
            _set_field(fields, f"{nome_mod}[lista][{idx}][excluido]", "1")
            if item.get("id"):
                _set_field(fields, f"{nome_mod}[lista][{idx}][id]", str(item["id"]))


def _verificar_anuncio_salvo(
    html_resposta: str,
    js_var: str,
    url_esperada: str,
    link_esperado: str = "",
) -> Dict[str, Any]:
    lista = _extrair_lista_js(html_resposta, js_var)
    ativos = [
        a for a in lista
        if str(a.get("excluido", "0")) != "1" and (a.get("url_imagem") or "").strip()
    ]
    for i, a in enumerate(ativos):
        url = (a.get("url_imagem") or "").strip()
        if url_esperada.split("/")[-1] in url or url == url_esperada:
            return {
                "salvo": True,
                "validado": True,
                "dom_slot_idx": lista.index(a) if a in lista else i,
                "url_imagem": url,
                "bandeiras": a.get("bandeiras") or [],
            }
    return {"salvo": False, "validado": False, "lista": lista}


def criar_anuncio_motorista(
    http: requests.Session,
    img_bytes: bytes,
    link_anuncio: str = "",
    selecionar_todas: bool = True,
    chave_secreta: Optional[str] = None,
    gerar_codigo_fn: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    nome_mod = "AnuncioAppTaxista"
    bandeira_id, fields, html = carregar_form_bandeira(http)
    lista = _extrair_lista_js(html, JS_LISTA_MOTORISTA)

    _ativar_exibir(fields, f"{nome_mod}[exibir_anuncio]")
    _marcar_excluidos_lista(fields, nome_mod, lista)

    novo_idx = len(lista) if lista else 0
    url_s3 = upload_imagem_configuracao(http, bandeira_id, img_bytes, "anuncio", "anuncio")
    ids = _ids_bandeiras(html, http, selecionar_todas)
    _preencher_slot_anuncio(
        fields, nome_mod, novo_idx, url_s3, (link_anuncio or "").strip(), ids,
    )

    html_save = salvar_bandeira(http, fields, chave_secreta, gerar_codigo_fn)
    verif = _verificar_anuncio_salvo(html_save, JS_LISTA_MOTORISTA, url_s3, link_anuncio)
    if not verif.get("salvo"):
        raise RuntimeError(
            "Upload OK, mas o anúncio motorista não persistiu no painel após gravar."
        )

    return {
        "sucesso": True,
        "mensagem": f"Anúncio motorista gravado na Machine. URL: {url_s3}",
        "verificacao": verif,
    }


def criar_anuncio_passageiro(
    http: requests.Session,
    img_bytes: bytes,
    link_anuncio: str,
    selecionar_todas: bool = True,
    bandeira_ids: Optional[List[str]] = None,
    chave_secreta: Optional[str] = None,
    gerar_codigo_fn: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    link_limpo = (link_anuncio or "").strip()
    if not link_limpo:
        return {"sucesso": False, "mensagem": "link_anuncio é obrigatório para passageiro."}

    nome_mod = "AnuncioTelaInicialAppPass"
    bandeira_id, fields, html = carregar_form_bandeira(http)
    lista = _extrair_lista_js(html, JS_LISTA_PASSAGEIRO)

    _ativar_exibir(fields, f"{nome_mod}[exibir_anuncio]")

    novo_idx, modo = _escolher_slot_passageiro(lista, html, bandeira_ids)
    if novo_idx is None:
        return {"sucesso": False, "mensagem": str(modo)}

    url_s3 = upload_imagem_configuracao(http, bandeira_id, img_bytes, "anuncio", "anuncio")
    ids = _ids_bandeiras(html, http, selecionar_todas, bandeira_ids)
    if not ids and not selecionar_todas:
        return {
            "sucesso": False,
            "mensagem": "Informe bandeira_ids ou selecionar_todas=true.",
        }

    _mesclar_lista_anuncios_no_form(fields, nome_mod, lista, novo_idx)
    item_id = str(lista[novo_idx].get("id") or "") if novo_idx < len(lista) else ""
    _preencher_slot_anuncio(
        fields, nome_mod, novo_idx, url_s3, link_limpo, ids, item_id=item_id,
    )

    html_save = salvar_bandeira(http, fields, chave_secreta, gerar_codigo_fn)
    verif = _verificar_anuncio_salvo(html_save, JS_LISTA_PASSAGEIRO, url_s3, link_limpo)
    if not verif.get("salvo"):
        raise RuntimeError(
            "Upload OK, mas o anúncio passageiro não persistiu no painel após gravar."
        )

    return {
        "sucesso": True,
        "mensagem": f"Anúncio passageiro gravado na Machine (slot {novo_idx}, {modo}). URL: {url_s3}",
        "verificacao": {**verif, "modo_slot": modo},
    }


def criar_campanha_corrida(
    http: requests.Session,
    img_bytes: bytes,
    link_campanha: str = "",
    selecionar_todas: bool = True,
    limite_corridas: int = 1000,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    chave_secreta: Optional[str] = None,
    gerar_codigo_fn: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    if not data_inicio:
        data_inicio = date.today().isoformat()
    if not data_fim:
        data_fim = (date.today() + timedelta(days=30)).isoformat()

    try:
        limite_str = str(int(limite_corridas))
    except (TypeError, ValueError):
        return {"sucesso": False, "mensagem": "limite_corridas deve ser inteiro positivo."}

    bandeira_id, fields, html = carregar_form_bandeira(http)
    lista = _extrair_lista_js(html, JS_LISTA_CAMPANHA)

    _ativar_exibir(fields, "Campanha[exibir_campanha]")

    novo_idx: Optional[int] = None
    for idx, item in enumerate(lista):
        if str(item.get("excluido", "0")) == "1":
            novo_idx = idx
            break
        if not item.get("url_imagem"):
            novo_idx = idx
            break
    if novo_idx is None:
        novo_idx = len(lista)

    url_s3 = upload_imagem_configuracao(http, bandeira_id, img_bytes, "campanha", "campanha")
    ids = _ids_bandeiras(html, http, selecionar_todas)
    item_id = str(lista[novo_idx].get("id") or "") if novo_idx < len(lista) else ""
    _preencher_slot_campanha(
        fields,
        novo_idx,
        url_s3,
        (link_campanha or "").strip(),
        limite_str,
        data_inicio,
        data_fim,
        ids,
        item_id=item_id,
    )

    html_save = salvar_bandeira(http, fields, chave_secreta, gerar_codigo_fn)
    lista_pos = _extrair_lista_js(html_save, JS_LISTA_CAMPANHA)
    salvo = any(
        str(c.get("excluido", "0")) != "1"
        and url_s3.split("/")[-1] in (c.get("url_imagem") or "")
        for c in lista_pos
    )
    if not salvo:
        raise RuntimeError(
            "Upload OK, mas a campanha no ciclo da corrida não persistiu após gravar."
        )

    preenchimento = {
        "dom_idx": novo_idx,
        "url_imagem": url_s3,
        "url_campanha": link_campanha or "",
        "limite": limite_str,
        "data_inicio": data_inicio,
        "data_fim": data_fim,
    }
    return {
        "sucesso": True,
        "mensagem": f"Campanha no ciclo da corrida gravada na Machine. URL: {url_s3}",
        "verificacao": {"preenchimento": preenchimento, "salvo": True, "validado": salvo},
    }


def remover_anuncio_motorista(
    http: requests.Session,
    chave_secreta: Optional[str] = None,
    gerar_codigo_fn: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    nome_mod = "AnuncioAppTaxista"
    _, fields, html = carregar_form_bandeira(http)
    lista = _extrair_lista_js(html, JS_LISTA_MOTORISTA)
    _marcar_excluidos_lista(fields, nome_mod, lista)
    _set_field(fields, f"{nome_mod}[exibir_anuncio]", "0")
    salvar_bandeira(http, fields, chave_secreta, gerar_codigo_fn)
    return {"sucesso": True, "mensagem": "Anúncio motorista removido via HTTP."}


def remover_anuncio_passageiro(
    http: requests.Session,
    indice: Optional[int] = None,
    chave_secreta: Optional[str] = None,
    gerar_codigo_fn: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    nome_mod = "AnuncioTelaInicialAppPass"
    _, fields, html = carregar_form_bandeira(http)
    lista = _extrair_lista_js(html, JS_LISTA_PASSAGEIRO)

    if indice is None:
        for idx, item in enumerate(lista):
            if str(item.get("excluido", "0")) != "1" and item.get("url_imagem"):
                _set_field(fields, f"{nome_mod}[lista][{idx}][excluido]", "1")
                if item.get("id"):
                    _set_field(fields, f"{nome_mod}[lista][{idx}][id]", str(item["id"]))
    else:
        alvo = int(indice)
        if 0 <= alvo < len(lista):
            _set_field(fields, f"{nome_mod}[lista][{alvo}][excluido]", "1")
            if lista[alvo].get("id"):
                _set_field(fields, f"{nome_mod}[lista][{alvo}][id]", str(lista[alvo]["id"]))
        else:
            return {"sucesso": False, "mensagem": f"Índice passageiro {indice} inválido."}

    salvar_bandeira(http, fields, chave_secreta, gerar_codigo_fn)
    return {"sucesso": True, "mensagem": "Anúncio(s) passageiro removido(s) via HTTP."}


def remover_campanha_corrida(
    http: requests.Session,
    indice: Optional[int] = None,
    chave_secreta: Optional[str] = None,
    gerar_codigo_fn: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    _, fields, html = carregar_form_bandeira(http)
    lista = _extrair_lista_js(html, JS_LISTA_CAMPANHA)

    if indice is None:
        _set_field(fields, "Campanha[exibir_campanha]", "0")
        for idx, item in enumerate(lista):
            if str(item.get("excluido", "0")) != "1":
                _set_field(fields, f"Campanha[lista][{idx}][excluido]", "1")
                if item.get("id"):
                    _set_field(fields, f"Campanha[lista][{idx}][id]", str(item["id"]))
    else:
        alvo = int(indice)
        if 0 <= alvo < len(lista):
            _set_field(fields, f"Campanha[lista][{alvo}][excluido]", "1")
            if lista[alvo].get("id"):
                _set_field(fields, f"Campanha[lista][{alvo}][id]", str(lista[alvo]["id"]))
        else:
            return {"sucesso": False, "mensagem": f"Índice campanha {indice} inválido."}

    salvar_bandeira(http, fields, chave_secreta, gerar_codigo_fn)
    return {"sucesso": True, "mensagem": "Campanha corrida removida/desativada via HTTP."}


def _login_e_sessao(
    email: str,
    senha: str,
    chave_secreta: Optional[str] = None,
) -> Tuple[requests.Session, Dict[str, Any]]:
    from auto_2fa import gerar_codigo, obter_chave

    chave = (chave_secreta or "").replace(" ", "") or obter_chave(email)
    login = login_painel(
        email=email,
        senha=senha,
        chave_secreta=chave or None,
        gerar_codigo_fn=gerar_codigo,
    )
    http = get_session(login["session_token"])
    if not http:
        raise RuntimeError("Falha ao recuperar sessão HTTP após login.")
    return http, login


def _resultado_base(email: str, login: Dict[str, Any], inner: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(inner)
    out.setdefault("email", email)
    out["chave_totp"] = login.get("chave_totp") or login.get("chave_secreta") or ""
    return out


def _executar_criar(
    email: str,
    senha: str,
    chave_secreta: Optional[str],
    imagem_path: str,
    criar_fn: Callable[..., Dict[str, Any]],
    **kwargs: Any,
) -> Dict[str, Any]:
    from auto_2fa import gerar_codigo

    with open(imagem_path, "rb") as fh:
        img = fh.read()
    http, login = _login_e_sessao(email, senha, chave_secreta)
    chave = (chave_secreta or login.get("chave_totp") or "").replace(" ", "")
    try:
        r = criar_fn(
            http,
            img,
            chave_secreta=chave or None,
            gerar_codigo_fn=gerar_codigo,
            **kwargs,
        )
    except RuntimeError as e:
        return _resultado_base(email, login, {"sucesso": False, "mensagem": str(e)})
    return _resultado_base(email, login, r)


def executar_adicionar_anuncio_motorista_http(
    email: str,
    senha: str,
    chave_secreta: Optional[str],
    imagem_path: str,
    link_anuncio: str = "",
    selecionar_todas: bool = True,
    **_: Any,
) -> Dict[str, Any]:
    return _executar_criar(
        email, senha, chave_secreta, imagem_path, criar_anuncio_motorista,
        link_anuncio=link_anuncio, selecionar_todas=selecionar_todas,
    )


def executar_adicionar_anuncio_passageiro_http(
    email: str,
    senha: str,
    chave_secreta: Optional[str],
    imagem_path: str,
    link_anuncio: str = "",
    selecionar_todas: bool = True,
    bandeira_ids: Optional[List[str]] = None,
    **_: Any,
) -> Dict[str, Any]:
    return _executar_criar(
        email, senha, chave_secreta, imagem_path, criar_anuncio_passageiro,
        link_anuncio=link_anuncio,
        selecionar_todas=selecionar_todas,
        bandeira_ids=bandeira_ids,
    )


def executar_adicionar_campanha_corrida_http(
    email: str,
    senha: str,
    chave_secreta: Optional[str],
    imagem_path: str,
    link_campanha: str = "",
    selecionar_todas: bool = True,
    limite_corridas: int = 1000,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    **_: Any,
) -> Dict[str, Any]:
    return _executar_criar(
        email, senha, chave_secreta, imagem_path, criar_campanha_corrida,
        link_campanha=link_campanha,
        selecionar_todas=selecionar_todas,
        limite_corridas=limite_corridas,
        data_inicio=data_inicio,
        data_fim=data_fim,
    )


def executar_remover_anuncio_motorista_http(
    email: str,
    senha: str,
    chave_secreta: Optional[str] = None,
    **_: Any,
) -> Dict[str, Any]:
    from auto_2fa import gerar_codigo

    http, login = _login_e_sessao(email, senha, chave_secreta)
    chave = (chave_secreta or login.get("chave_totp") or "").replace(" ", "")
    try:
        r = remover_anuncio_motorista(http, chave_secreta=chave or None, gerar_codigo_fn=gerar_codigo)
    except RuntimeError as e:
        return _resultado_base(email, login, {"sucesso": False, "mensagem": str(e)})
    return _resultado_base(email, login, r)


def executar_remover_anuncio_passageiro_http(
    email: str,
    senha: str,
    chave_secreta: Optional[str] = None,
    indice: Optional[int] = None,
    **_: Any,
) -> Dict[str, Any]:
    from auto_2fa import gerar_codigo

    http, login = _login_e_sessao(email, senha, chave_secreta)
    chave = (chave_secreta or login.get("chave_totp") or "").replace(" ", "")
    try:
        r = remover_anuncio_passageiro(
            http, indice=indice, chave_secreta=chave or None, gerar_codigo_fn=gerar_codigo,
        )
    except RuntimeError as e:
        return _resultado_base(email, login, {"sucesso": False, "mensagem": str(e)})
    return _resultado_base(email, login, r)


def executar_remover_campanha_corrida_http(
    email: str,
    senha: str,
    chave_secreta: Optional[str] = None,
    indice: Optional[int] = None,
    **_: Any,
) -> Dict[str, Any]:
    from auto_2fa import gerar_codigo

    http, login = _login_e_sessao(email, senha, chave_secreta)
    chave = (chave_secreta or login.get("chave_totp") or "").replace(" ", "")
    try:
        r = remover_campanha_corrida(
            http, indice=indice, chave_secreta=chave or None, gerar_codigo_fn=gerar_codigo,
        )
    except RuntimeError as e:
        return _resultado_base(email, login, {"sucesso": False, "mensagem": str(e)})
    return _resultado_base(email, login, r)
