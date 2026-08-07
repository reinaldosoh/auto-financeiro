"""
Cliente HTTP para banners em /bandeira/update (Recursos Premium).

Substitui Selenium nos fluxos motorista, passageiro e campanha no ciclo da corrida.
Reutiliza login e sessão de machine_notificacao_http.
"""

from __future__ import annotations

import io
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
    autenticar_acao_2fa,
    get_session,
    login_painel,
)

log = logging.getLogger(__name__)

REFERER_BANDEIRA = BASE_URL + "/bandeira/update"


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


def _indices_campo(fields: Dict[str, List[str]], prefix: str, suffix: str) -> List[int]:
    pat = re.compile(rf"^{re.escape(prefix)}_(\d+)_{re.escape(suffix)}$")
    idxs: List[int] = []
    for name in fields:
        m = pat.match(name)
        if m:
            idxs.append(int(m.group(1)))
    return sorted(set(idxs))


def _slot_ocupado(fields: Dict[str, List[str]], prefix: str, idx: int) -> bool:
    excl = (fields.get(f"{prefix}_{idx}_excluido") or ["0"])[0]
    if str(excl).strip() == "1":
        return False
    img = (fields.get(f"{prefix}_{idx}_url_imagem") or [""])[0].strip()
    link = (fields.get(f"{prefix}_{idx}_url_anuncio") or [""])[0].strip()
    url_c = (fields.get(f"{prefix}_{idx}_url_campanha") or [""])[0].strip()
    return bool(img or link or url_c)


def _opcoes_select(html: str, select_name: str) -> List[str]:
    block_pat = rf'<select[^>]+name="{re.escape(select_name)}"[^>]*>(.*?)</select>'
    m = re.search(block_pat, html, re.S | re.I)
    if not m:
        block_pat = rf'<select[^>]+name="{re.escape(select_name)}\[\]"[^>]*>(.*?)</select>'
        m = re.search(block_pat, html, re.S | re.I)
    if not m:
        return []
    opts = re.findall(r'<option[^>]+value="([^"]+)"', m.group(1), re.I)
    return [o for o in opts if o and o != "multiselect-all"]


def _selecionar_todas_bandeiras(
    fields: Dict[str, List[str]],
    html: str,
    select_name: str,
    selecionar_todas: bool,
    bandeira_ids: Optional[List[str]] = None,
) -> None:
    if not selecionar_todas and not bandeira_ids:
        return
    ids = bandeira_ids or _opcoes_select(html, select_name)
    if not ids:
        ids = _opcoes_select(html, select_name + "[]")
    if ids:
        fields[select_name] = list(ids)
        fields[select_name + "[]"] = list(ids)


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


def _resposta_precisa_2fa_acao(response: requests.Response) -> bool:
    if "/bandeira/update" not in response.url and response.status_code == 200:
        lower = (response.text or "").lower()
        if "loginform" in lower and "bandeira-form" not in lower:
            return False
    lower = (response.text or "").lower()
    return (
        "autenticacao necessária" in lower
        or "autenticacao necessaria" in lower
        or "autenticação necessária" in lower
        or ("auth-modal" in lower and "bandeira" in lower)
    )


def salvar_bandeira(
    http: requests.Session,
    fields: Dict[str, List[str]],
    chave_secreta: Optional[str] = None,
    gerar_codigo_fn: Optional[Callable[[str], str]] = None,
) -> None:
    pairs: List[Tuple[str, str]] = []
    for name, values in fields.items():
        for val in values:
            pairs.append((name, val))

    if not any(n.startswith("yt") for n in fields):
        pairs.append(("yt0", "Gravar"))

    r = http.post(
        BASE_URL + "/bandeira/update",
        data=pairs,
        headers={"Referer": REFERER_BANDEIRA},
        timeout=180,
        allow_redirects=True,
    )

    if _resposta_precisa_2fa_acao(r):
        autenticar_acao_2fa(
            http,
            chave_secreta=chave_secreta,
            gerar_codigo_fn=gerar_codigo_fn,
        )
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

    lower = (r.text or "").lower()
    if "erro" in lower and "sucesso" not in lower and len(r.text or "") < 5000:
        if "errorMessage" in r.text:
            raise RuntimeError("Erro ao gravar configurações da bandeira.")


def _ativar_exibir(fields: Dict[str, List[str]], prefix: str, sim: bool = True) -> None:
    _set_field(fields, f"{prefix}_exibir_anuncio_0", "1" if sim else "0")
    _set_field(fields, f"{prefix}_exibir_anuncio_1", "0" if sim else "1")
    if sim:
        _set_field(fields, f"{prefix}_exibir_anuncio", "0")


def criar_anuncio_motorista(
    http: requests.Session,
    img_bytes: bytes,
    link_anuncio: str = "",
    selecionar_todas: bool = True,
    chave_secreta: Optional[str] = None,
    gerar_codigo_fn: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    prefix = "AnuncioAppTaxista"
    tipo = "tela_inicial_app_taxista"
    bandeira_id, fields, html = carregar_form_bandeira(http)

    _ativar_exibir(fields, prefix, True)

    idxs = _indices_campo(fields, prefix, "url_imagem")
    if not idxs:
        idxs = [0]

    for idx in idxs:
        if _slot_ocupado(fields, prefix, idx):
            _set_field(fields, f"{prefix}_{idx}_excluido", "1")

    novo_idx = max(idxs) + 1 if idxs else 1
    if novo_idx <= max(idxs):
        novo_idx = max(idxs) + 1

    url_s3 = upload_imagem_configuracao(http, bandeira_id, img_bytes, "anuncio", "anuncio")
    _set_field(fields, f"{prefix}_{novo_idx}_url_imagem", url_s3)
    _set_field(fields, f"{prefix}_{novo_idx}_excluido", "0")
    if link_anuncio:
        _set_field(fields, f"{prefix}_{novo_idx}_url_anuncio", link_anuncio.strip())

    sel = f"filtro_bandeiras_anuncio_{tipo}_{novo_idx}"
    _selecionar_todas_bandeiras(fields, html, sel, selecionar_todas)

    salvar_bandeira(http, fields, chave_secreta=chave_secreta, gerar_codigo_fn=gerar_codigo_fn)

    return {
        "sucesso": True,
        "mensagem": f"Anúncio motorista configurado via HTTP. URL: {url_s3}",
        "verificacao": {"url_imagem": url_s3, "dom_idx": novo_idx},
    }


def criar_anuncio_passageiro(
    http: requests.Session,
    img_bytes: bytes,
    link_anuncio: str,
    selecionar_todas: bool = True,
    chave_secreta: Optional[str] = None,
    gerar_codigo_fn: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    link_limpo = (link_anuncio or "").strip()
    if not link_limpo:
        return {"sucesso": False, "mensagem": "link_anuncio é obrigatório para passageiro."}

    prefix = "AnuncioTelaInicialAppPass"
    tipo = "tela_inicial_app_passageiro"
    bandeira_id, fields, html = carregar_form_bandeira(http)

    _ativar_exibir(fields, prefix, True)

    idxs = _indices_campo(fields, prefix, "url_imagem")
    if not idxs:
        idxs = [0]

    novo_idx: Optional[int] = None
    for idx in idxs:
        if not _slot_ocupado(fields, prefix, idx):
            novo_idx = idx
            break

    if novo_idx is None:
        if len(idxs) >= 3:
            return {
                "sucesso": False,
                "mensagem": "Limite de 3 anúncios passageiros no painel.",
            }
        novo_idx = max(idxs) + 1

    url_s3 = upload_imagem_configuracao(http, bandeira_id, img_bytes, "anuncio", "anuncio")
    _set_field(fields, f"{prefix}_{novo_idx}_url_imagem", url_s3)
    _set_field(fields, f"{prefix}_{novo_idx}_excluido", "0")
    _set_field(fields, f"{prefix}_{novo_idx}_url_anuncio", link_limpo)

    sel = f"filtro_bandeiras_anuncio_{tipo}_{novo_idx}"
    _selecionar_todas_bandeiras(fields, html, sel, selecionar_todas)

    salvar_bandeira(http, fields, chave_secreta=chave_secreta, gerar_codigo_fn=gerar_codigo_fn)

    return {
        "sucesso": True,
        "mensagem": f"Anúncio passageiro configurado via HTTP. URL: {url_s3}",
        "verificacao": {"dom_slot_idx": novo_idx, "url_imagem": url_s3},
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
    prefix = "Campanha"
    if not data_inicio:
        data_inicio = date.today().isoformat()
    if not data_fim:
        data_fim = (date.today() + timedelta(days=30)).isoformat()

    try:
        limite_str = str(int(limite_corridas))
    except (TypeError, ValueError):
        return {"sucesso": False, "mensagem": "limite_corridas deve ser inteiro positivo."}

    bandeira_id, fields, html = carregar_form_bandeira(http)

    _set_field(fields, f"{prefix}_exibir_campanha_0", "1")
    _set_field(fields, f"{prefix}_exibir_campanha_1", "0")
    _set_field(fields, f"{prefix}_exibir_campanha", "0")

    idxs = _indices_campo(fields, prefix, "url_imagem")
    if not idxs:
        idxs = [0]

    novo_idx: Optional[int] = None
    for idx in idxs:
        if not _slot_ocupado(fields, prefix, idx):
            novo_idx = idx
            break
    if novo_idx is None:
        novo_idx = max(idxs) + 1

    url_s3 = upload_imagem_configuracao(http, bandeira_id, img_bytes, "campanha", "campanha")
    _set_field(fields, f"{prefix}_{novo_idx}_url_imagem", url_s3)
    _set_field(fields, f"{prefix}_{novo_idx}_excluido", "0")
    if link_campanha:
        _set_field(fields, f"{prefix}_{novo_idx}_url_campanha", link_campanha.strip())
    _set_field(fields, f"{prefix}_{novo_idx}_limite_solicitacoes_finalizadas", limite_str)
    _set_field(fields, f"{prefix}_{novo_idx}_data_hora_inicio", data_inicio)
    _set_field(fields, f"{prefix}_{novo_idx}_data_hora_fim", data_fim)

    sel = f"filtro_bandeiras_campanha_{novo_idx}"
    _selecionar_todas_bandeiras(fields, html, sel, selecionar_todas)

    salvar_bandeira(http, fields, chave_secreta=chave_secreta, gerar_codigo_fn=gerar_codigo_fn)

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
        "mensagem": f"Campanha no ciclo da corrida gravada via HTTP. URL: {url_s3}",
        "verificacao": {"preenchimento": preenchimento, "salvo": True, "validado": True},
    }


def remover_anuncio_motorista(
    http: requests.Session,
    chave_secreta: Optional[str] = None,
    gerar_codigo_fn: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    prefix = "AnuncioAppTaxista"
    _, fields, _ = carregar_form_bandeira(http)

    idxs = _indices_campo(fields, prefix, "url_imagem")
    for idx in idxs:
        if _slot_ocupado(fields, prefix, idx):
            _set_field(fields, f"{prefix}_{idx}_excluido", "1")

    _ativar_exibir(fields, prefix, False)
    salvar_bandeira(http, fields, chave_secreta=chave_secreta, gerar_codigo_fn=gerar_codigo_fn)
    return {"sucesso": True, "mensagem": "Anúncio motorista removido via HTTP."}


def remover_anuncio_passageiro(
    http: requests.Session,
    indice: Optional[int] = None,
    chave_secreta: Optional[str] = None,
    gerar_codigo_fn: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    prefix = "AnuncioTelaInicialAppPass"
    _, fields, _ = carregar_form_bandeira(http)
    idxs = _indices_campo(fields, prefix, "url_imagem")

    if indice is None:
        for idx in idxs:
            if _slot_ocupado(fields, prefix, idx):
                _set_field(fields, f"{prefix}_{idx}_excluido", "1")
    else:
        alvo = int(indice)
        if alvo in idxs:
            _set_field(fields, f"{prefix}_{alvo}_excluido", "1")
        elif 0 <= alvo < len(idxs):
            _set_field(fields, f"{prefix}_{idxs[alvo]}_excluido", "1")
        else:
            return {"sucesso": False, "mensagem": f"Índice passageiro {indice} inválido."}

    salvar_bandeira(http, fields, chave_secreta=chave_secreta, gerar_codigo_fn=gerar_codigo_fn)
    return {"sucesso": True, "mensagem": "Anúncio(s) passageiro removido(s) via HTTP."}


def remover_campanha_corrida(
    http: requests.Session,
    indice: Optional[int] = None,
    chave_secreta: Optional[str] = None,
    gerar_codigo_fn: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    prefix = "Campanha"
    _, fields, _ = carregar_form_bandeira(http)

    if indice is None:
        _set_field(fields, f"{prefix}_exibir_campanha_1", "1")
        _set_field(fields, f"{prefix}_exibir_campanha_0", "0")
        for idx in _indices_campo(fields, prefix, "url_imagem"):
            _set_field(fields, f"{prefix}_{idx}_excluido", "1")
    else:
        alvo = int(indice)
        idxs = _indices_campo(fields, prefix, "url_imagem")
        if alvo in idxs:
            _set_field(fields, f"{prefix}_{alvo}_excluido", "1")
        elif 0 <= alvo < len(idxs):
            _set_field(fields, f"{prefix}_{idxs[alvo]}_excluido", "1")
        else:
            return {"sucesso": False, "mensagem": f"Índice campanha {indice} inválido."}

    salvar_bandeira(http, fields, chave_secreta=chave_secreta, gerar_codigo_fn=gerar_codigo_fn)
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


def executar_adicionar_anuncio_motorista_http(
    email: str,
    senha: str,
    chave_secreta: Optional[str],
    imagem_path: str,
    link_anuncio: str = "",
    selecionar_todas: bool = True,
    **_: Any,
) -> Dict[str, Any]:
    from auto_2fa import gerar_codigo

    with open(imagem_path, "rb") as fh:
        img = fh.read()
    http, login = _login_e_sessao(email, senha, chave_secreta)
    chave = (chave_secreta or login.get("chave_totp") or "").replace(" ", "")
    r = criar_anuncio_motorista(
        http, img, link_anuncio, selecionar_todas,
        chave_secreta=chave or None, gerar_codigo_fn=gerar_codigo,
    )
    return _resultado_base(email, login, r)


def executar_adicionar_anuncio_passageiro_http(
    email: str,
    senha: str,
    chave_secreta: Optional[str],
    imagem_path: str,
    link_anuncio: str = "",
    selecionar_todas: bool = True,
    **_: Any,
) -> Dict[str, Any]:
    from auto_2fa import gerar_codigo

    with open(imagem_path, "rb") as fh:
        img = fh.read()
    http, login = _login_e_sessao(email, senha, chave_secreta)
    chave = (chave_secreta or login.get("chave_totp") or "").replace(" ", "")
    r = criar_anuncio_passageiro(
        http, img, link_anuncio, selecionar_todas,
        chave_secreta=chave or None, gerar_codigo_fn=gerar_codigo,
    )
    return _resultado_base(email, login, r)


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
    from auto_2fa import gerar_codigo

    with open(imagem_path, "rb") as fh:
        img = fh.read()
    http, login = _login_e_sessao(email, senha, chave_secreta)
    chave = (chave_secreta or login.get("chave_totp") or "").replace(" ", "")
    r = criar_campanha_corrida(
        http, img, link_campanha, selecionar_todas, limite_corridas,
        data_inicio, data_fim,
        chave_secreta=chave or None, gerar_codigo_fn=gerar_codigo,
    )
    return _resultado_base(email, login, r)


def executar_remover_anuncio_motorista_http(
    email: str,
    senha: str,
    chave_secreta: Optional[str] = None,
    **_: Any,
) -> Dict[str, Any]:
    from auto_2fa import gerar_codigo

    http, login = _login_e_sessao(email, senha, chave_secreta)
    chave = (chave_secreta or login.get("chave_totp") or "").replace(" ", "")
    r = remover_anuncio_motorista(http, chave_secreta=chave or None, gerar_codigo_fn=gerar_codigo)
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
    r = remover_anuncio_passageiro(
        http, indice=indice, chave_secreta=chave or None, gerar_codigo_fn=gerar_codigo,
    )
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
    r = remover_campanha_corrida(
        http, indice=indice, chave_secreta=chave or None, gerar_codigo_fn=gerar_codigo,
    )
    return _resultado_base(email, login, r)
