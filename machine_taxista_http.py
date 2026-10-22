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


class PainelErro(RuntimeError):
    """Erro do painel com código estável e status HTTP sugerido para a API."""

    def __init__(self, codigo: str, mensagem: str, status: int = 400):
        super().__init__(mensagem)
        self.codigo = codigo
        self.status = status


# Atributos de uma tag, respeitando aspas (um ">" dentro de value="..." não fecha a tag).
_ATTRS = r"(?:[^>\"']|\"[^\"]*\"|'[^']*')*"


def _attr(attrs: str, name: str) -> Optional[str]:
    """Valor decodificado do atributo (aspas duplas, simples ou sem aspas); None se ausente."""
    m = re.search(
        r"(?:^|[\s\"'])" + re.escape(name) + r"\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))",
        attrs,
        re.I,
    )
    if not m:
        return None
    return unescape(next(g for g in m.groups() if g is not None))


def _flag(attrs: str, name: str) -> bool:
    """Atributo booleano (disabled, checked, selected...) fora de valores entre aspas."""
    sem_valores = re.sub(r"\"[^\"]*\"|'[^']*'", '""', attrs)
    return re.search(r"(?:^|[\s\"'])" + name + r"(?=[\s/=>]|$)", sem_valores, re.I) is not None


class _TaxistaFormParser:
    """Extrai campos do formulário de edição do taxista como o navegador enviaria.

    Campos `disabled` não são enviados (igual ao navegador); valores HTML são decodificados.
    """

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
    def parse(
        cls, html: str, ignorados: Optional[List[str]] = None
    ) -> Dict[str, List[str]]:
        """`ignorados` (opcional) recebe os nomes dos campos desabilitados que não são enviados."""
        form = cls._form_html(html)
        fields: Dict[str, List[str]] = defaultdict(list)

        def _ignora(nome: str) -> None:
            if ignorados is not None and nome not in ignorados:
                ignorados.append(nome)

        for im in re.finditer(r"<input\b(" + _ATTRS + r")>", form, flags=re.I):
            attrs = im.group(1)
            name = _attr(attrs, "name")
            if not name:
                continue
            typ = (_attr(attrs, "type") or "text").lower()
            if typ in ("submit", "button", "file", "image", "reset"):
                continue
            if _flag(attrs, "disabled"):
                _ignora(name)
                continue
            val = _attr(attrs, "value")
            if typ in ("checkbox", "radio"):
                if _flag(attrs, "checked"):
                    fields[name].append("on" if val is None else val)
            else:
                fields[name].append(val or "")

        for sm in re.finditer(
            r"<select\b(" + _ATTRS + r")>(.*?)</select>", form, re.S | re.I
        ):
            attrs = sm.group(1)
            name = _attr(attrs, "name")
            if not name:
                continue
            if _flag(attrs, "disabled"):
                _ignora(name)
                continue
            opcoes: List[Tuple[str, bool]] = []
            for om in re.finditer(r"<option\b(" + _ATTRS + r")>([^<]*)", sm.group(2), re.I):
                if _flag(om.group(1), "disabled"):
                    continue
                val = _attr(om.group(1), "value")
                if val is None:
                    val = unescape(om.group(2)).strip()
                opcoes.append((val, _flag(om.group(1), "selected")))
            if _flag(attrs, "multiple"):
                fields_sel = [v for v, sel in opcoes if sel]
            else:
                marcados = [v for v, sel in opcoes if sel]
                if marcados:
                    fields_sel = [marcados[-1]]
                else:
                    fields_sel = [opcoes[0][0]] if opcoes else []
            for v in fields_sel:
                fields[name].append(v)

        for tm in re.finditer(
            r"<textarea\b(" + _ATTRS + r")>(.*?)</textarea>", form, re.S | re.I
        ):
            name = _attr(tm.group(1), "name")
            if not name:
                continue
            if _flag(tm.group(1), "disabled"):
                _ignora(name)
                continue
            conteudo = unescape(tm.group(2))
            if conteudo.startswith("\n"):
                conteudo = conteudo[1:]
            fields[name].append(conteudo)

        return dict(fields)

    @classmethod
    def list_selects(cls, html: str) -> List[Dict[str, Any]]:
        form = cls._form_html(html)
        out: List[Dict[str, Any]] = []
        for sm in re.finditer(
            r"<select\b(" + _ATTRS + r")>(.*?)</select>", form, re.S | re.I
        ):
            name = _attr(sm.group(1), "name")
            if not name:
                continue
            options: List[Dict[str, Any]] = []
            for om in re.finditer(
                r"<option\b(" + _ATTRS + r")>(.*?)</option>", sm.group(2), re.S | re.I
            ):
                text = unescape(re.sub(r"<[^>]+>", "", om.group(2))).strip()
                val = _attr(om.group(1), "value")
                if val is None:
                    val = text
                options.append(
                    {"value": val, "text": text, "selected": _flag(om.group(1), "selected")}
                )
            out.append(
                {"name": name, "options": options, "disabled": _flag(sm.group(1), "disabled")}
            )
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


def _autenticar_acao_taxista(
    http: requests.Session,
    referer_url: str,
    chave_secreta: Optional[str],
    gerar_codigo_fn: Optional[Any],
) -> None:
    code = None
    if chave_secreta and gerar_codigo_fn:
        code = gerar_codigo_fn(chave_secreta)
    if not code:
        autenticar_acao_2fa(
            http,
            chave_secreta=chave_secreta,
            gerar_codigo_fn=gerar_codigo_fn,
        )
        return
    r = http.post(
        BASE_URL + "/site/autenticarUsuario2FA",
        data={"code": code},
        headers={"Referer": referer_url},
        timeout=30,
    )
    from machine_notificacao_http import _parse_json_response

    data = _parse_json_response(r)
    if not data.get("success"):
        msg = data.get("message") or "Falha na autenticação 2FA da ação."
        if data.get("usuarioBloqueado"):
            raise RuntimeError(f"Usuário bloqueado: {msg}")
        raise RuntimeError(msg)


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


def _normalizar_telefone_taxista(valor: str) -> str:
    """Painel rejeita +55 no POST; formato (DD) 9XXXX-XXXX grava."""
    digitos = re.sub(r"\D+", "", valor or "")
    if digitos.startswith("55") and len(digitos) >= 12:
        digitos = digitos[2:]
    if len(digitos) == 11:
        return f"({digitos[:2]}) {digitos[2:7]}-{digitos[7:]}"
    if len(digitos) == 10:
        return f"({digitos[:2]}) {digitos[2:6]}-{digitos[6:]}"
    return (valor or "").strip()


def _telefone_como_navegador(fields: Dict[str, List[str]]) -> None:
    """Reproduz o que o JavaScript da página faz com o telefone antes de enviar.

    O HTML traz "+55 31 98734-0913"; o intl-tel-input (separateDialCode) mostra e posta só a parte
    nacional ("31 98734-0913") e o submit grava `full_phone` em formato internacional. Enviar o valor
    cru faz o painel responder "Telefone é inválido". O número em si não muda.
    """
    tel = (fields.get("Taxista[telefone]") or [""])[0]
    m = re.match(r"^\+(\d{1,3})\s+(.+)$", tel.strip())
    if not m:
        return
    _set_field(fields, "Taxista[telefone]", m.group(2).strip())
    _set_field(fields, "full_phone", "+" + re.sub(r"\D+", "", tel))


def _preparar_campos_taxista_post(fields: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """Posta o formulário como o navegador enviaria: só o campo-alvo muda.

    Não reescreve o vínculo (o painel o desabilita). O telefone só troca de formato, como no navegador.
    """
    _telefone_como_navegador(fields)
    return _limpar_campos_para_post(fields)


def _limpar_campos_para_post(fields: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """Remove linhas template do painel (ex.: Veiculos[__INDEX__]) que quebram o POST Yii."""
    out: Dict[str, List[str]] = {}
    for name, values in fields.items():
        if "[__INDEX__]" in name or name.endswith("[__INDEX__]"):
            continue
        cleaned = [v for v in values if v is not None]
        if cleaned:
            out[name] = cleaned
    return out


def _extrair_botao_submit(html: str) -> Tuple[str, str]:
    """Botão Gravar do painel: name=yt0, value=Gravar (Yii)."""
    form = _TaxistaFormParser._form_html(html)
    m = re.search(
        r'<button[^>]+type="submit"[^>]+name="(yt\d+)"[^>]*>([^<]*)</button>',
        form,
        re.I,
    )
    if m:
        return m.group(1), unescape(m.group(2)).strip() or "Gravar"
    m = re.search(
        r'<input[^>]+type="submit"[^>]+name="(yt\d+)"[^>]*value="([^"]*)"',
        form,
        re.I,
    )
    if m:
        return m.group(1), m.group(2)
    return "yt0", "Gravar"


def _pairs_from_fields(
    fields: Dict[str, List[str]], html: str
) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    for name, values in fields.items():
        for val in values:
            pairs.append((name, val))
    btn_name, btn_val = _extrair_botao_submit(html)
    pairs.append((btn_name, btn_val))
    return pairs


def _valor_campo_no_html(html: str, field_name: str) -> str:
    for sel in _TaxistaFormParser.list_selects(html):
        if sel["name"] != field_name:
            continue
        for opt in sel["options"]:
            if opt.get("selected"):
                return str(opt.get("value") or "")
    parsed = _TaxistaFormParser.parse(html)
    vals = parsed.get(field_name) or []
    return vals[0] if vals else ""


# Campos que o painel recalcula a cada leitura (não são alteração do cadastro).
_CAMPOS_RUIDO = {
    "data_status_taxista",
    "logado",
    "situacao",
    "tempo_de_impedimento",
    "motivo_impedimento",
    "ultima_corrida",
    "ultima_atividade",
}


def _campo_ruido(nome: str) -> bool:
    return nome in _CAMPOS_RUIDO or "csrf" in nome.lower()


def diff_formularios(
    antes: Dict[str, List[str]], depois: Dict[str, List[str]]
) -> List[Dict[str, Any]]:
    """Campos cujo valor mudou entre dois estados do formulário (ignora campos de ruído)."""
    out: List[Dict[str, Any]] = []
    for nome in sorted(set(antes) | set(depois)):
        if _campo_ruido(nome) or "[__INDEX__]" in nome:
            continue
        va, vd = antes.get(nome, []), depois.get(nome, [])
        if va != vd:
            out.append({"campo": nome, "antes": va, "depois": vd})
    return out


def classificar_erro(exc: BaseException) -> Tuple[str, int]:
    """(codigo, status HTTP) estáveis para o cliente distinguir a falha."""
    if isinstance(exc, PainelErro):
        return exc.codigo, exc.status
    if isinstance(exc, requests.RequestException):
        return "painel_indisponivel", 502
    msg = _norm_label(str(exc))
    if "saldo minimo" in msg:
        return "saldo_minimo", 422
    if "bandeira" in msg and "desativada" in msg:
        return "bandeira_desativada", 403
    if "2fa" in msg and ("segredo" in msg or "chave" in msg):
        return "2fa_sem_segredo", 409
    if "bloqueado" in msg:
        return "usuario_bloqueado", 403
    if "sessao expirada" in msg or "faca login" in msg:
        return "sessao_expirada", 401
    if "http 403" in msg or "sem permissao" in msg:
        return "sem_permissao", 403
    return "erro_painel", 400


def carregar_form_taxista(http: requests.Session, taxista_id: int) -> Tuple[Dict[str, List[str]], str]:
    url = f"{BASE_URL}/taxista/update/{int(taxista_id)}"
    r = http.get(url, timeout=120)
    if r.status_code == 403:
        raise PainelErro("sem_permissao", "HTTP 403 em /taxista/update — conta sem permissão.", 403)
    text = r.text or ""
    if "LoginForm" in text and "Taxista[" not in text:
        raise PainelErro("sessao_expirada", "Sessão expirada — faça login novamente.", 401)
    if "Taxista[" not in text:
        raise PainelErro(
            "taxista_nao_encontrado",
            f"Página do taxista {taxista_id} não encontrada ou sem formulário.",
            404,
        )
    fields = _TaxistaFormParser.parse(text)
    return fields, text


def _select_desabilitado(html: str, field_name: str) -> bool:
    return any(
        sel["name"] == field_name and sel.get("disabled")
        for sel in _TaxistaFormParser.list_selects(html)
    )


def _erros_do_painel(text: str) -> List[str]:
    erros = re.findall(r'class="errorMessage[^"]*"[^>]*>([^<]+)', text)
    erros += re.findall(r'class="help-block error"[^>]*>([^<]+)', text)
    if "errorSummary" in text:
        m = re.search(r'class="errorSummary[^"]*"[^>]*>[\s\S]*?<ul>([\s\S]*?)</ul>', text, re.I)
        if m:
            erros += re.findall(r"<li[^>]*>([^<]+)", m.group(1))
    return [e.strip() for e in erros if e.strip()]


def atualizar_relacao_taxista(
    http: requests.Session,
    taxista_id: int,
    relacao: RelacaoTaxista,
    *,
    chave_secreta: Optional[str] = None,
    gerar_codigo_fn: Optional[Any] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Altera só `taxista_apoio`; devolve o diff completo antes/depois.

    - Já no valor pedido: não posta (`inalterado`).
    - `dry_run`: lê e monta o POST, sem gravar nem pedir 2FA.
    - `alteracoes_inesperadas`: campos que mudaram além do alvo (deve vir vazio).
    """
    ignorados: List[str] = []
    html = carregar_form_taxista(http, taxista_id)[1]
    fields = _TaxistaFormParser.parse(html, ignorados)
    field_name, value_map = descobrir_campo_relacao(html)
    if relacao not in value_map:
        raise RuntimeError(f"Valor de relação '{relacao}' não mapeado para o campo {field_name}.")
    if _select_desabilitado(html, field_name):
        raise PainelErro(
            "campo_desabilitado",
            f"O campo {field_name} está desabilitado para esta conta ou motorista.",
            403,
        )
    antes = (fields.get(field_name) or [""])[0]
    novo = value_map[relacao]
    base = {
        "taxista_id": int(taxista_id),
        "relacao": relacao,
        "campo": field_name,
        "valor_anterior": antes,
        "valor_aplicado": novo,
        "dry_run": dry_run,
    }

    if antes == novo:
        return {**base, "inalterado": True, "alteracoes": [], "alteracoes_inesperadas": []}

    base_navegador = _preparar_campos_taxista_post({k: list(v) for k, v in fields.items()})
    a_postar = {k: list(v) for k, v in fields.items()}
    _set_field(a_postar, field_name, novo)
    a_postar = _preparar_campos_taxista_post(a_postar)
    previstas = diff_formularios(base_navegador, a_postar)

    if dry_run:
        return {
            **base,
            "inalterado": False,
            "alteracoes_previstas": previstas,
            "campos_enviados": sum(len(v) for v in a_postar.values()),
            "campos_desabilitados_nao_enviados": sorted(ignorados),
        }

    url = f"{BASE_URL}/taxista/update/{int(taxista_id)}"
    pairs = _pairs_from_fields(a_postar, html)
    headers = {
        "Referer": url,
        "Content-Type": "application/x-www-form-urlencoded",
    }

    if chave_secreta and gerar_codigo_fn:
        _autenticar_acao_taxista(http, url, chave_secreta, gerar_codigo_fn)

    r = http.post(url, data=pairs, headers=headers, timeout=180, allow_redirects=True)
    if _resposta_precisa_2fa_acao(r):
        log.info("Gravar taxista pediu 2FA de ação — autenticando…")
        _autenticar_acao_taxista(http, url, chave_secreta, gerar_codigo_fn)
        r = http.post(url, data=pairs, headers=headers, timeout=180, allow_redirects=True)

    text = r.text or ""
    if "LoginForm" in text and "Taxista[" not in text:
        raise PainelErro("sessao_expirada", "Sessão expirada ao gravar taxista.", 401)

    erros = _erros_do_painel(text)
    if erros:
        msg = "; ".join(erros)
        codigo, status = classificar_erro(RuntimeError(msg))
        if codigo == "erro_painel":
            codigo, status = "validacao_painel", 422
        raise PainelErro(codigo, msg, status)

    fields_depois, html_depois = carregar_form_taxista(http, taxista_id)
    gravado = _valor_campo_no_html(html_depois, field_name)
    if gravado != novo:
        raise PainelErro(
            "painel_nao_gravou",
            f"Painel não gravou {field_name}: esperado value={novo!r}, lido={gravado!r} após POST.",
            409,
        )

    alteracoes = diff_formularios(fields, fields_depois)
    inesperadas = [a for a in alteracoes if a["campo"] != field_name]
    if inesperadas:
        log.warning(
            "taxista %s: campos alterados além de %s: %s",
            taxista_id,
            field_name,
            [a["campo"] for a in inesperadas],
        )

    log.info(
        "taxista %s relação %s: campo=%s %s -> %s",
        taxista_id,
        relacao,
        field_name,
        antes,
        novo,
    )
    return {
        **base,
        "inalterado": False,
        "alteracoes": alteracoes,
        "alteracoes_inesperadas": inesperadas,
    }


# ---------------------------------------------------------------------------
# Listagem de motoristas por relação (GET da grade /taxista/index)
# ---------------------------------------------------------------------------

STATUS_TAXISTA_TODOS = ("A", "I", "S", "G", "R", "D", "E", "F")
RELACAO_FILTRO = {"prioritario": "0", "secundario": "1", "todos": ""}
_STATUS_PADRAO = ("A",)
MAX_PAGINAS_LISTAGEM = 400


def _celula(row: str, classe: str) -> str:
    m = re.search(r'class="' + classe + r'"[^>]*>(.*?)</td>', row, re.S | re.I)
    if not m:
        return ""
    texto = re.sub(r"<br\s*/?>", "|", m.group(1), flags=re.I)
    texto = re.sub(r"<[^>]+>", "", texto)
    return re.sub(r"\s+", " ", unescape(texto)).strip()


def parse_grade_taxistas(html: str) -> Dict[str, Any]:
    """Linhas e total da grade /taxista/index (Yii)."""
    total = 0
    m = re.search(r"de\s+([\d.]+)\s+resultado", html)
    if m:
        total = int(m.group(1).replace(".", ""))
    itens: List[Dict[str, Any]] = []
    for rm in re.finditer(r'<tr class="(?:odd|even)">(.*?)</tr>', html, re.S | re.I):
        row = rm.group(1)
        im = re.search(r"<input\b(" + _ATTRS + r'name="alteracao-massiva-taxista-id\[\]"' + _ATTRS + r")>", row)
        taxista_id = _attr(im.group(1), "value") if im else None
        if not taxista_id or not taxista_id.isdigit():
            continue
        cats = [c.strip() for c in _celula(row, "col-categoria").split("|") if c.strip()]
        itens.append(
            {
                "taxista_id": int(taxista_id),
                "nome": _celula(row, "col-nome"),
                "status": _celula(row, "col-status"),
                "categorias": cats,
            }
        )
    return {"total": total, "itens": itens}


def listar_taxistas(
    http: requests.Session,
    *,
    relacao: str = "todos",
    status: Optional[List[str]] = None,
    pagina: int = 1,
    todas: bool = False,
) -> Dict[str, Any]:
    """Lista motoristas filtrando por Relação (prioritário/secundário) e status.

    Só leitura. A grade tem 10 por página; `todas=True` percorre todas as páginas.
    O status é sempre enviado (sem ele o painel usa um filtro salvo na sessão).
    """
    if relacao not in RELACAO_FILTRO:
        raise PainelErro("parametro_invalido", f"relacao deve ser uma de {sorted(RELACAO_FILTRO)}.", 422)
    sts = [s.strip().upper() for s in (status or _STATUS_PADRAO) if s.strip()]
    if not sts or any(s not in STATUS_TAXISTA_TODOS for s in sts):
        raise PainelErro(
            "parametro_invalido", f"status deve usar as letras {list(STATUS_TAXISTA_TODOS)}.", 422
        )
    if pagina < 1:
        raise PainelErro("parametro_invalido", "pagina deve ser >= 1.", 422)

    r = http.get(
        BASE_URL + "/taxista/index",
        headers={"Referer": BASE_URL + "/", "Accept": "text/html,application/xhtml+xml"},
        timeout=60,
    )
    if r.status_code == 403:
        raise PainelErro("sem_permissao", "HTTP 403 em /taxista/index — conta sem permissão.", 403)
    texto = r.text or ""
    if "taxista-form" not in texto:
        if "LoginForm" in texto or "site/login" in (getattr(r, "url", "") or ""):
            raise PainelErro("sessao_expirada", "Sessão expirada — faça login novamente.", 401)
        raise PainelErro("erro_painel", "Formulário de filtros de /taxista/index não encontrado.", 400)

    campos = _TaxistaFormParser.parse(texto)
    campos["TaxistaSearch[taxista_apoio]"] = [RELACAO_FILTRO[relacao]]
    campos["TaxistaSearch[status_taxista][]"] = sts
    pairs = [(k, v) for k, vals in campos.items() for v in vals]
    headers = {
        "Referer": BASE_URL + "/taxista/index",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    def _pagina(n: int) -> Dict[str, Any]:
        url = BASE_URL + "/taxista/index" + (f"?TaxistaSearch_page={n}" if n > 1 else "")
        resp = http.post(url, data=pairs, headers=headers, timeout=90)
        t = resp.text or ""
        if "LoginForm" in t and "taxista-form" not in t:
            raise PainelErro("sessao_expirada", "Sessão expirada — faça login novamente.", 401)
        return parse_grade_taxistas(t)

    primeira = _pagina(1 if todas else pagina)
    total = primeira["total"]
    por_pagina = 10
    paginas = max(1, -(-total // por_pagina)) if total else 1
    itens = list(primeira["itens"])
    atual = 1 if todas else pagina
    if todas:
        if paginas > MAX_PAGINAS_LISTAGEM:
            raise PainelErro(
                "lista_grande_demais",
                f"{total} motoristas ({paginas} páginas): use `pagina` ou o export assíncrono.",
                422,
            )
        for n in range(2, paginas + 1):
            itens.extend(_pagina(n)["itens"])
    return {
        "relacao": relacao,
        "status": sts,
        "total": total,
        "pagina": atual,
        "paginas": paginas,
        "por_pagina": por_pagina,
        "todas": todas,
        "itens": itens,
        "completo": todas and len(itens) == total,
    }
