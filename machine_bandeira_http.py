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


class _BandeiraFormParser:
    """Extrai campos estáticos do #bandeira-form (regex; ignora <script>)."""

    @staticmethod
    def _form_html(html: str) -> str:
        marker = html.find('id="bandeira-form"')
        if marker < 0:
            raise RuntimeError("Formulário bandeira-form não encontrado.")
        start = html.rfind("<form", 0, marker)
        end = html.find("</form>", start)
        if end < 0:
            raise RuntimeError("Fim do formulário bandeira-form não encontrado.")
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

        for tm in re.finditer(
            r'<textarea[^>]+name="([^"]+)"[^>]*>(.*?)</textarea>', form, re.S | re.I
        ):
            fields[tm.group(1)].append(tm.group(2))

        return dict(fields)


def _extrair_opcoes_veiculos_proximidade(html: str) -> List[Dict[str, str]]:
    opcoes: List[Dict[str, str]] = []
    for bloco in re.findall(r"OPCOES_VEICULOS_PROXIMIDADE\.push\(\{([^}]+)\}", html):
        opt: Dict[str, str] = {}
        for chave in ("veiculos", "distancia", "taxiApoio", "taxiParceiro"):
            m = re.search(
                rf"{chave}:\s*'([^']*)'|{chave}:\s*parseInt\('(\d+)'\)", bloco
            )
            if m:
                opt[chave] = m.group(1) if m.group(1) is not None else m.group(2)
        if opt:
            opcoes.append(opt)
    return opcoes


def _mesclar_despacho_veiculos(fields: Dict[str, List[str]], html: str) -> None:
    """Campos de despacho renderizados via OPCOES_VEICULOS_PROXIMIDADE (JS)."""
    opcoes = _extrair_opcoes_veiculos_proximidade(html)
    for idx, opt in enumerate(opcoes, start=1):
        if opt.get("veiculos") is not None:
            _set_field(fields, f"Bandeira[taxis_simultaneos_{idx}]", opt["veiculos"])
        if opt.get("distancia") is not None:
            _set_field(fields, f"Bandeira[distancia_taxis_{idx}]", opt["distancia"])
        for campo, chave in (
            ("inclui_taxi_apoio", "taxiApoio"),
            ("inclui_taxi_parceiras", "taxiParceiro"),
        ):
            nome = f"Bandeira[{campo}_{idx}]"
            marcado = str(opt.get(chave, "0")) == "1"
            fields[nome] = ["0"] + (["1"] if marcado else [])
        if idx == 1:
            _set_field(fields, "Bandeira[taxis_area_1]", "1")
            _set_field(fields, "Bandeira[taxis_ponto_apoio_1]", "1")


def _mesclar_mensagens_personalizadas(fields: Dict[str, List[str]], html: str) -> None:
    contadores: Dict[str, int] = defaultdict(int)
    for msg, tipo in re.findall(r'addNovaMensagem\("([^"]*)"\s*,\s*\'([PC])\'\)', html):
        idx = contadores[tipo]
        _set_field(fields, f"MensagemPersonalizada[{tipo}][{idx}]", msg)
        contadores[tipo] += 1


def _mesclar_marketplace_bandeiras(fields: Dict[str, List[str]], html: str) -> None:
    ids = re.findall(r"listaSelecionados\.push\((\d+)\)", html)
    if ids:
        fields["BandeiraConfiguracao[utilizar_marketplace_agrupadora_bandeiras][]"] = ids


def _mesclar_area_permissiva(fields: Dict[str, List[str]], html: str) -> None:
    if "BandeiraConfiguracao[area_permissiva_id]" in fields:
        return
    m = re.search(
        r'name="BandeiraConfiguracao\[area_permissiva_id\]"[^>]*>(.*?)</select>',
        html,
        re.S,
    )
    if not m:
        return
    for om in re.finditer(r'<option([^>]*)value="([^"]*)"', m.group(1)):
        if "selected" in om.group(1):
            _set_field(fields, "BandeiraConfiguracao[area_permissiva_id]", om.group(2))
            return
    _set_field(fields, "BandeiraConfiguracao[area_permissiva_id]", "0")


def _aplicar_item_lista_anuncio(
    fields: Dict[str, List[str]],
    nome_mod: str,
    idx: int,
    item: Dict[str, Any],
) -> None:
    base = f"{nome_mod}[lista][{idx}]"
    for extra in (f"{base}[bandeira_id]", f"{base}[tipo_anuncio]", f"{base}[url_anuncio]"):
        fields.pop(extra, None)
    if str(item.get("excluido", "0")) == "1":
        _set_field(fields, f"{base}[excluido]", "1")
        if item.get("id"):
            _set_field(fields, f"{base}[id]", str(item["id"]))
        return
    _set_field(fields, f"{base}[url_imagem]", (item.get("url_imagem") or "").strip())
    _set_field(fields, f"{base}[excluido]", str(item.get("excluido") or "0"))
    _set_field(fields, f"{base}[ativo]", str(item.get("ativo") or "1"))
    if item.get("id"):
        _set_field(fields, f"{base}[id]", str(item["id"]))
    else:
        _set_field(fields, f"{base}[id]", "")
    if item.get("permite_alterar_url_anuncio", True) and item.get("url_anuncio"):
        _set_field(fields, f"{base}[url_anuncio]", str(item["url_anuncio"]).strip())
    bs = item.get("bandeiras") or []
    if bs:
        fields[f"{base}[bandeiras][]"] = [str(b) for b in bs]


def _limpar_formato_lista_motorista(fields: Dict[str, List[str]]) -> None:
    """Motorista usa AnuncioAppTaxista_{idx}_*, não [lista][idx]."""
    for k in list(fields.keys()):
        if k.startswith("AnuncioAppTaxista[lista]"):
            del fields[k]


def _aplicar_item_motorista(
    fields: Dict[str, List[str]],
    idx: int,
    item: Dict[str, Any],
) -> None:
    """Campos flat do painel: AnuncioAppTaxista_{idx}_url_imagem etc."""
    prefix = f"AnuncioAppTaxista_{idx}"
    filtro = f"filtro_bandeiras_anuncio_tela_inicial_app_taxista_{idx}"
    for k in (
        f"{prefix}_url_imagem",
        f"{prefix}_url_anuncio",
        f"{prefix}_excluido",
        f"{prefix}_id",
        f"{filtro}[]",
    ):
        fields.pop(k, None)
    if str(item.get("excluido", "0")) == "1":
        _set_field(fields, f"{prefix}_excluido", "1")
        if item.get("id"):
            _set_field(fields, f"{prefix}_id", str(item["id"]))
        return
    _set_field(fields, f"{prefix}_url_imagem", (item.get("url_imagem") or "").strip())
    _set_field(fields, f"{prefix}_excluido", str(item.get("excluido") or "0"))
    if item.get("id"):
        _set_field(fields, f"{prefix}_id", str(item["id"]))
    if item.get("url_anuncio"):
        _set_field(fields, f"{prefix}_url_anuncio", str(item["url_anuncio"]).strip())
    bs = item.get("bandeiras") or []
    if bs:
        fields[f"{filtro}[]"] = [str(b) for b in bs]


def _preencher_slot_motorista(
    fields: Dict[str, List[str]],
    idx: int,
    url_imagem: str,
    link: str,
    bandeira_ids: List[str],
    excluido: str = "0",
    item_id: str = "",
) -> None:
    prefix = f"AnuncioAppTaxista_{idx}"
    filtro = f"filtro_bandeiras_anuncio_tela_inicial_app_taxista_{idx}"
    _set_field(fields, f"{prefix}_url_imagem", url_imagem)
    _set_field(fields, f"{prefix}_excluido", excluido)
    if link:
        _set_field(fields, f"{prefix}_url_anuncio", link.strip())
    if item_id:
        _set_field(fields, f"{prefix}_id", item_id)
    if bandeira_ids:
        fields[f"{filtro}[]"] = [str(b) for b in bandeira_ids if str(b).strip()]


def _mesclar_listas_recursos_premium(fields: Dict[str, List[str]], html: str) -> None:
    """Replica campos dinâmicos de anúncios/campanhas a partir das variáveis JS."""
    lista_pass = _extrair_lista_js(html, JS_LISTA_PASSAGEIRO)
    if lista_pass:
        _ativar_exibir(fields, "AnuncioTelaInicialAppPass[exibir_anuncio]")
        for idx, item in enumerate(lista_pass):
            _aplicar_item_lista_anuncio(fields, "AnuncioTelaInicialAppPass", idx, item)

    lista_mot = _extrair_lista_js(html, JS_LISTA_MOTORISTA)
    if lista_mot:
        _ativar_exibir(fields, "AnuncioAppTaxista[exibir_anuncio]")
        for idx, item in enumerate(lista_mot):
            _aplicar_item_lista_anuncio(fields, "AnuncioAppTaxista", idx, item)

    lista_camp = _extrair_lista_js(html, JS_LISTA_CAMPANHA)
    if lista_camp:
        _ativar_exibir(fields, "Campanha[exibir_campanha]")
        for idx, item in enumerate(lista_camp):
            base = f"Campanha[lista][{idx}]"
            if str(item.get("excluido", "0")) == "1":
                _set_field(fields, f"{base}[excluido]", "1")
                if item.get("id"):
                    _set_field(fields, f"{base}[id]", str(item["id"]))
                continue
            _set_field(fields, f"{base}[url_imagem]", (item.get("url_imagem") or "").strip())
            _set_field(fields, f"{base}[url_campanha]", (item.get("url_campanha") or "").strip())
            _set_field(fields, f"{base}[excluido]", str(item.get("excluido") or "0"))
            _set_field(fields, f"{base}[ativo]", str(item.get("ativo") or "1"))
            if item.get("id"):
                _set_field(fields, f"{base}[id]", str(item["id"]))
            for k in ("limite_solicitacoes_finalizadas", "data_hora_inicio", "data_hora_fim"):
                if item.get(k) is not None:
                    _set_field(fields, f"{base}[{k}]", str(item[k]))
            bs = item.get("bandeiras") or []
            if bs:
                fields[f"{base}[bandeiras][]"] = [str(b) for b in bs]


def _completar_form_bandeira(fields: Dict[str, List[str]], html: str) -> None:
    """Adiciona campos gerados por JS que o GET estático não inclui no parser."""
    _mesclar_despacho_veiculos(fields, html)
    _mesclar_mensagens_personalizadas(fields, html)
    _mesclar_marketplace_bandeiras(fields, html)
    _mesclar_area_permissiva(fields, html)
    _mesclar_listas_recursos_premium(fields, html)


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
    fields = _BandeiraFormParser.parse(r.text)
    _completar_form_bandeira(fields, r.text)
    total_pares = sum(len(v) for v in fields.values())
    log.debug(
        "Form bandeira carregado: %d chaves, %d pares",
        len(fields),
        total_pares,
    )
    return bandeira_id, fields, r.text


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
    """Sim = hidden vazio + radio '1' (formato exato do #bandeira-form)."""
    fields[nome_campo] = ["", "1"]


def _preencher_slot_anuncio(
    fields: Dict[str, List[str]],
    nome_mod: str,
    idx: int,
    url_imagem: str,
    link: str,
    bandeira_ids: List[str],
    item_id: str = "",
    excluido: str = "0",
    incluir_url_anuncio: bool = True,
) -> None:
    """Campos iguais ao obterHTMLAnuncio / exibirAnuncio (sem bandeira_id/tipo_anuncio)."""
    base = f"{nome_mod}[lista][{idx}]"
    _set_field(fields, f"{base}[url_imagem]", url_imagem)
    if incluir_url_anuncio:
        _set_field(fields, f"{base}[url_anuncio]", link)
    elif f"{base}[url_anuncio]" in fields:
        del fields[f"{base}[url_anuncio]"]
    _set_field(fields, f"{base}[excluido]", excluido)
    _set_field(fields, f"{base}[ativo]", "1")
    if item_id:
        _set_field(fields, f"{base}[id]", item_id)
    else:
        _set_field(fields, f"{base}[id]", "")
    for extra in (f"{base}[bandeira_id]", f"{base}[tipo_anuncio]"):
        fields.pop(extra, None)
    if bandeira_ids:
        fields[f"{base}[bandeiras][]"] = list(bandeira_ids)


def _mesclar_lista_anuncios_no_form(
    fields: Dict[str, List[str]],
    nome_mod: str,
    lista: List[Dict[str, Any]],
    idx_editar: int,
) -> None:
    """Preserva anúncios existentes no POST (formato do serialize do #bandeira-form)."""
    for idx, item in enumerate(lista):
        if idx == idx_editar:
            continue
        _aplicar_item_lista_anuncio(fields, nome_mod, idx, item)


def _escolher_slot_passageiro(
    lista: List[Dict[str, Any]],
    html: str,
) -> Tuple[Optional[int], Optional[str]]:
    """
    Escolhe índice para um anúncio passageiro NOVO.
    Nunca reutiliza slot de bandeira existente — só slot vazio ou append (adicionarNovoAnuncio).
    """
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


def _confirmar_persistencia_passageiro(
    http: requests.Session,
    url_esperada: str,
    link_esperado: str,
    idx_esperado: int,
    qtd_antes: int,
) -> Dict[str, Any]:
    """Confirma persistência com GET real (POST response pode mentir)."""
    r = http.get(BASE_URL + "/bandeira/update", timeout=120)
    lista = _extrair_lista_js(r.text or "", JS_LISTA_PASSAGEIRO)
    link_norm = (link_esperado or "").strip().rstrip("/").lower()
    nome_arquivo = url_esperada.split("/")[-1]

    def _ok(a: Dict[str, Any], i: int) -> Optional[Dict[str, Any]]:
        url = (a.get("url_imagem") or "").strip()
        link = (a.get("url_anuncio") or "").strip().rstrip("/").lower()
        link_ok = not link_norm or link == link_norm or link_norm in link
        img_ok = bool(url) and (
            nome_arquivo in url
            or url == url_esperada
            or "/upload/anuncios/bandeira/" in url
        )
        if link_ok and (img_ok or link_norm):
            return {
                "salvo": True,
                "validado": True,
                "dom_slot_idx": i,
                "url_imagem": url,
                "bandeiras": a.get("bandeiras") or [],
                "lista_total": len(lista),
            }
        return None

    for i, a in enumerate(lista):
        if str(a.get("excluido", "0")) == "1":
            continue
        hit = _ok(a, i)
        if hit:
            return hit

    if len(lista) > qtd_antes and 0 <= idx_esperado < len(lista):
        a = lista[idx_esperado]
        if str(a.get("excluido", "0")) != "1":
            hit = _ok(a, idx_esperado)
            if hit:
                return hit

    return {
        "salvo": False,
        "validado": False,
        "dom_slot_idx": idx_esperado,
        "lista_total": len(lista),
        "qtd_antes": qtd_antes,
    }


def _confirmar_persistencia_motorista(
    http: requests.Session,
    url_esperada: str,
    idx_esperado: int,
) -> Dict[str, Any]:
    """Confirma persistência com GET real; rejeita URL /tmp/."""
    r = http.get(BASE_URL + "/bandeira/update", timeout=120)
    lista = _extrair_lista_js(r.text or "", JS_LISTA_MOTORISTA)
    nome_arquivo = url_esperada.split("/")[-1]

    for i, a in enumerate(lista):
        if str(a.get("excluido", "0")) == "1":
            continue
        url = (a.get("url_imagem") or "").strip()
        if not url or "/tmp/" in url:
            continue
        permanente = "/upload/anuncios/bandeira/" in url and "/tmp/" not in url
        if permanente or nome_arquivo in url:
            return {
                "salvo": True,
                "validado": True,
                "dom_slot_idx": i,
                "url_imagem": url,
                "bandeiras": a.get("bandeiras") or [],
                "lista_total": len(lista),
            }

    if 0 <= idx_esperado < len(lista):
        a = lista[idx_esperado]
        url = (a.get("url_imagem") or "").strip()
        if str(a.get("excluido", "0")) != "1" and url and "/tmp/" not in url:
            return {
                "salvo": True,
                "validado": True,
                "dom_slot_idx": idx_esperado,
                "url_imagem": url,
                "bandeiras": a.get("bandeiras") or [],
                "lista_total": len(lista),
            }

    return {
        "salvo": False,
        "validado": False,
        "dom_slot_idx": idx_esperado,
        "lista_total": len(lista),
    }


def _confirmar_remocao_motorista(http: requests.Session) -> Dict[str, Any]:
    r = http.get(BASE_URL + "/bandeira/update", timeout=120)
    lista = _extrair_lista_js(r.text or "", JS_LISTA_MOTORISTA)
    ativos = [
        i for i, a in enumerate(lista)
        if str(a.get("excluido", "0")) != "1" and (a.get("url_imagem") or "").strip()
    ]
    return {
        "removido": len(ativos) == 0,
        "ativos_restantes": len(ativos),
        "lista_total": len(lista),
    }


def criar_anuncio_motorista(
    http: requests.Session,
    img_bytes: bytes,
    link_anuncio: str = "",
    selecionar_todas: bool = True,
    bandeira_ids: Optional[List[str]] = None,
    chave_secreta: Optional[str] = None,
    gerar_codigo_fn: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    nome_mod = "AnuncioAppTaxista"
    bandeira_id, fields, html = carregar_form_bandeira(http)
    lista = _extrair_lista_js(html, JS_LISTA_MOTORISTA)
    _limpar_formato_lista_motorista(fields)
    _ativar_exibir(fields, f"{nome_mod}[exibir_anuncio]")

    ids = _ids_bandeiras(html, http, selecionar_todas, bandeira_ids)
    if not ids and not selecionar_todas:
        return {
            "sucesso": False,
            "mensagem": "Informe bandeira_ids ou selecionar_todas=true.",
        }

    tem_ativo = any(
        str(a.get("excluido", "0")) != "1" and (a.get("url_imagem") or "").strip()
        for a in lista
    )
    if tem_ativo and lista:
        antigo = dict(lista[0])
        antigo["excluido"] = "1"
        _aplicar_item_lista_anuncio(fields, nome_mod, 0, antigo)
        novo_idx = 1
        item_id = ""
    else:
        novo_idx = 0
        item_id = ""

    url_s3 = upload_imagem_configuracao(http, bandeira_id, img_bytes, "anuncio", "anuncio")
    _preencher_slot_anuncio(
        fields,
        nome_mod,
        novo_idx,
        url_s3,
        (link_anuncio or "").strip(),
        ids,
        item_id=item_id,
    )

    form_pares = sum(len(v) for v in fields.values())
    salvar_bandeira(http, fields, chave_secreta, gerar_codigo_fn)
    verif = _confirmar_persistencia_motorista(http, url_s3, novo_idx)
    if not verif.get("salvo"):
        dica = (
            " Redeploy a VPS com banner_http_form_completo=true."
            if form_pares < 450
            else ""
        )
        raise RuntimeError(
            "Upload OK, mas o anúncio motorista não persistiu no painel após gravar "
            f"(slot {novo_idx}, form_pares={form_pares}).{dica}"
        )

    return {
        "sucesso": True,
        "mensagem": f"Anúncio motorista gravado na Machine (slot {novo_idx}). URL: {verif.get('url_imagem') or url_s3}",
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

    novo_idx, modo = _escolher_slot_passageiro(lista, html)
    if novo_idx is None:
        return {"sucesso": False, "mensagem": str(modo)}

    url_s3 = upload_imagem_configuracao(http, bandeira_id, img_bytes, "anuncio", "anuncio")
    ids = _ids_bandeiras(html, http, selecionar_todas, bandeira_ids)
    if not ids and not selecionar_todas:
        return {
            "sucesso": False,
            "mensagem": "Informe bandeira_ids ou selecionar_todas=true.",
        }

    item_id = str(lista[novo_idx].get("id") or "") if novo_idx < len(lista) else ""
    _preencher_slot_anuncio(
        fields, nome_mod, novo_idx, url_s3, link_limpo, ids,
        item_id=item_id,
    )

    form_pares = sum(len(v) for v in fields.values())
    html_save = salvar_bandeira(http, fields, chave_secreta, gerar_codigo_fn)
    verif = _confirmar_persistencia_passageiro(
        http, url_s3, link_limpo, novo_idx, len(lista),
    )
    if not verif.get("salvo"):
        dica = (
            " Redeploy a VPS (ghcr.io/reinaldosoh/auto-financeiro:latest) — "
            "form incompleto (<450 pares) não persiste no painel."
            if form_pares < 450
            else ""
        )
        raise RuntimeError(
            "Upload OK, mas o anúncio passageiro não persistiu no painel após gravar "
            f"(slot {novo_idx}, modo {modo}, form_pares={form_pares}).{dica}"
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


def _confirmar_remocao_passageiro(
    http: requests.Session,
    indices: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Confirma exclusão com GET real (POST response pode mentir)."""
    r = http.get(BASE_URL + "/bandeira/update", timeout=120)
    lista = _extrair_lista_js(r.text or "", JS_LISTA_PASSAGEIRO)
    if indices is None:
        ativos = [
            i
            for i, a in enumerate(lista)
            if str(a.get("excluido", "0")) != "1" and (a.get("url_imagem") or "").strip()
        ]
        return {
            "removido": len(ativos) == 0,
            "ativos_restantes": len(ativos),
            "lista_total": len(lista),
        }
    ok = all(
        0 <= i < len(lista) and str(lista[i].get("excluido", "0")) == "1"
        for i in indices
    )
    return {"removido": ok, "indices": indices, "lista_total": len(lista)}


def _marcar_slots_excluidos_passageiro(
    fields: Dict[str, List[str]],
    nome_mod: str,
    lista: List[Dict[str, Any]],
    indices: List[int],
) -> None:
    """Mescla a lista inteira no POST e marca slot(s) com excluido=1."""
    _ativar_exibir(fields, f"{nome_mod}[exibir_anuncio]")
    alvos = set(indices)
    for idx, item in enumerate(lista):
        copia = dict(item)
        if idx in alvos:
            copia["excluido"] = "1"
        _aplicar_item_lista_anuncio(fields, nome_mod, idx, copia)


def remover_anuncio_motorista(
    http: requests.Session,
    chave_secreta: Optional[str] = None,
    gerar_codigo_fn: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    nome_mod = "AnuncioAppTaxista"
    _, fields, html = carregar_form_bandeira(http)
    lista = _extrair_lista_js(html, JS_LISTA_MOTORISTA)
    _limpar_formato_lista_motorista(fields)
    _marcar_excluidos_lista(fields, nome_mod, lista)
    _set_field(fields, f"{nome_mod}[exibir_anuncio]", "0")
    form_pares = sum(len(v) for v in fields.values())
    salvar_bandeira(http, fields, chave_secreta, gerar_codigo_fn)
    verif = _confirmar_remocao_motorista(http)
    if not verif.get("removido"):
        raise RuntimeError(
            f"Remoção motorista não persistiu (ainda {verif.get('ativos_restantes')} ativo(s), "
            f"form_pares={form_pares})."
        )
    return {"sucesso": True, "mensagem": "Anúncio motorista removido via HTTP.", "verificacao": verif}


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
        indices = [
            idx
            for idx, item in enumerate(lista)
            if str(item.get("excluido", "0")) != "1" and (item.get("url_imagem") or "").strip()
        ]
        if not indices:
            return {"sucesso": True, "mensagem": "Nenhum anúncio passageiro ativo para remover."}
    else:
        alvo = int(indice)
        if alvo < 0 or alvo >= len(lista):
            return {"sucesso": False, "mensagem": f"Índice passageiro {indice} inválido."}
        if str(lista[alvo].get("excluido", "0")) == "1":
            return {
                "sucesso": True,
                "mensagem": f"Slot passageiro {alvo} já estava excluído.",
                "verificacao": {"removido": True, "indices": [alvo]},
            }
        indices = [alvo]

    _marcar_slots_excluidos_passageiro(fields, nome_mod, lista, indices)
    form_pares = sum(len(v) for v in fields.values())
    salvar_bandeira(http, fields, chave_secreta, gerar_codigo_fn)
    verif = _confirmar_remocao_passageiro(http, indices)
    if not verif.get("removido"):
        dica = (
            " Redeploy a VPS com banner_http_form_completo=true."
            if form_pares < 450
            else ""
        )
        raise RuntimeError(
            f"Remoção não persistiu no painel (slots {indices}, form_pares={form_pares}).{dica}"
        )
    return {
        "sucesso": True,
        "mensagem": f"Anúncio(s) passageiro removido(s) via HTTP (slots {indices}).",
        "verificacao": verif,
    }


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
    bandeira_ids: Optional[List[str]] = None,
    **_: Any,
) -> Dict[str, Any]:
    return _executar_criar(
        email, senha, chave_secreta, imagem_path, criar_anuncio_motorista,
        link_anuncio=link_anuncio,
        selecionar_todas=selecionar_todas,
        bandeira_ids=bandeira_ids,
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
