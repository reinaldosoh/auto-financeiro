"""
Cliente HTTP para o painel TaxiMachine — Consultar corridas (/solicitacao/historicoCorridas2).

Usa cookie de sessão (PHPSESSID), mesmo padrão de machine_notificacao_http.py.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

import requests

from machine_notificacao_http import BASE_URL

log = logging.getLogger(__name__)

STATUS_ATIVOS_MAPA = frozenset(
    {
        "Em espera",
        "Aguardando aceite",
        "Aguardando passageiro",
        "A caminho",
        "Em andamento",
    }
)

_STATUS_TEXTO = {
    "P": "Pendente",
    "D": "Em despacho",
    "S": "Aguardando aceite",
    "A": "A caminho",
    "E": "Em andamento",
    "F": "Finalizada",
    "C": "Cancelada",
    "W": "Em espera",
}


def _parse_json_response(resp: requests.Response) -> Dict[str, Any]:
    try:
        return resp.json()
    except Exception:
        return {}


def _ensure_session(http: requests.Session) -> None:
    r = http.get(BASE_URL + "/solicitacao/historicoCorridas2?resetSesion=1", timeout=60)
    if r.status_code != 200 or "LoginForm" in r.text[:2000]:
        raise RuntimeError("Sessão inválida ou expirada — faça login novamente.")


def obter_bandeiras_historico(http: requests.Session) -> List[Dict[str, str]]:
    """Bandeiras disponíveis no filtro da tela Consultar corridas."""
    _ensure_session(http)
    r = http.get(BASE_URL + "/solicitacao/historicoCorridas2?resetSesion=1", timeout=60)
    items: List[Dict[str, str]] = []
    for m in re.finditer(
        r"<a id=['\"]?(\d+)['\"]?[^>]*onclick=['\"]realizaFiltroExterno\(id\)[^>]*>([^<]+)</a>",
        r.text,
    ):
        bid, nome = m.group(1), m.group(2).strip()
        if bid in ("0", "1875"):
            continue
        items.append({"id": bid, "nome": nome})
    return items


def aplicar_filtro_corridas(
    http: requests.Session,
    *,
    bandeira_id: Optional[str] = None,
    horas: float = 0.25,
    filtro_matriz: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Aplica filtro padrão (intervalo + bandeira) via POST /solicitacao/relatorioCorridas.
    horas: 0.25 = 15 min, 0.5 = 30 min, 1 = 1h, etc.
    """
    _ensure_session(http)
    data: Dict[str, Any] = {
        "tipo": "corrida",
        "filtroDefaultHora": horas,
        "filtroDefault": True,
    }
    matriz = filtro_matriz if filtro_matriz is not None else bandeira_id
    if matriz is not None and str(matriz) != "":
        data["filtro_matriz"] = str(matriz)

    r = http.post(BASE_URL + "/solicitacao/relatorioCorridas", data=data, timeout=60)
    payload = _parse_json_response(r)
    if not payload.get("success"):
        raise RuntimeError(payload.get("message") or "Falha ao aplicar filtro de corridas.")
    return payload


def _text(html: str) -> str:
    t = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", t).strip()


# Ícone de intercorrência na grade (coluna de indicadores) — NÃO confundir com hora/celular.
_ALERTA_ICON_RE = re.compile(
    r"(?:class=\"[^\"]*(?:alerta|intercorr|warning|atencao|glyphicon-warning|fa-exclamation)[^\"]*\""
    r"|src=\"[^\"]*(?:alerta|intercorr|warning|atencao)[^\"]*\")",
    re.I,
)


def _parse_coluna_indicadores(html: str) -> Dict[str, Any]:
    """Coluna 1 da grade: ícone de alerta (se houver), hora e ícone de app."""
    tem_alerta = bool(_ALERTA_ICON_RE.search(html or ""))
    hora_m = re.search(r"\b(\d{2}:\d{2}:\d{2})\b", html or "")
    return {
        "tem_alerta": tem_alerta,
        "hora": hora_m.group(1) if hora_m else "",
    }


def _coord_valida_br(lat: Any, lng: Any) -> bool:
    try:
        la, ln = float(lat), float(lng)
    except (TypeError, ValueError):
        return False
    if not (-90 <= la <= 90 and -180 <= ln <= 180):
        return False
    # Brasil continental + margem
    return -35.5 <= la <= 6.0 and -75.0 <= ln <= -28.0


def _normalizar_coord_br(lat: Any, lng: Any) -> Tuple[Optional[float], Optional[float]]:
    """Descarta coords inválidas e corrige lat/lng invertidos quando óbvio."""
    try:
        a, b = float(lat), float(lng)
    except (TypeError, ValueError):
        return None, None
    if _coord_valida_br(a, b):
        return a, b
    if _coord_valida_br(b, a):
        return b, a
    return None, None


def _parse_intercorrencias(html: str) -> List[str]:
    """Caixas amarelas de alerta no painel de detalhes da corrida."""
    msgs: List[str] = []
    for block in re.finditer(
        r'class="[^"]*(?:box-alert|alert-warning|alert-danger|alert-info|alerta|mensagem-alerta|intercorr)[^"]*"[^>]*>(.*?)</(?:div|section|ul)>',
        html,
        re.S | re.I,
    ):
        t = _text(block.group(1))
        if t and len(t) > 8:
            msgs.append(t)
    for m in re.finditer(r"<li[^>]*>(.*?)</li>", html, re.S):
        ctx = html[max(0, m.start() - 300) : m.start()]
        if re.search(r"alert|intercorr|warning|atencao", ctx, re.I):
            t = _text(m.group(1))
            if t and len(t) > 8:
                msgs.append(t)
    return list(dict.fromkeys(msgs))


def _parse_linha_corrida(row_html: str) -> Optional[Dict[str, Any]]:
    os_id = re.search(r'class="identificador">(\d+)', row_html)
    if not os_id:
        return None
    cols = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S)
    if len(cols) < 9:
        return None

    indicadores = _parse_coluna_indicadores(cols[1])

    status_m = re.search(r'class="status([^"]*)"><p>([^<]+)', row_html)
    status_classe = (status_m.group(1) if status_m else "").strip()
    status_texto = (status_m.group(2) if status_m else _text(cols[8])).strip()

    empresa_txt = _text(cols[8]) if len(cols) > 8 else ""
    return {
        "id": os_id.group(1),
        "os": _text(cols[4]) or os_id.group(1),
        "tem_alerta": indicadores["tem_alerta"],
        "hora": indicadores["hora"],
        "alerta": indicadores["hora"],
        "partida": _text(cols[5]),
        "passageiro": _text(cols[6]),
        "motorista": _text(cols[7]) if _text(cols[7]) != "---" else None,
        "empresa": empresa_txt if empresa_txt and empresa_txt != "---" else None,
        "status": status_texto,
        "status_classe": status_classe,
        "ativo_mapa": status_texto in STATUS_ATIVOS_MAPA,
    }


def _parse_grid_render(render: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    rows = re.findall(r'<tr class="(?:odd|even)">(.*?)</tr>', render, re.S)
    corridas = [c for c in (_parse_linha_corrida(r) for r in rows) if c]
    summary_m = re.search(r'class="summary">([^<]+)', render)
    summary = summary_m.group(1).strip() if summary_m else ""
    total = None
    total_m = re.search(r"de\s+([\d\.]+)\s+resultados?", summary)
    if total_m:
        total = int(total_m.group(1).replace(".", ""))
    paginas = len(re.findall(r'href="[^"]*page=(\d+)', render))
    meta = {"summary": summary, "total": total, "paginas": max(paginas, 1)}
    return corridas, meta


def listar_corridas(
    http: requests.Session,
    *,
    page: int = 1,
    incluir_coordenadas: bool = False,
    apenas_ativos_mapa: bool = False,
    max_coordenadas: int = 40,
) -> Dict[str, Any]:
    """Lista corridas da grade via POST /solicitacao/historicoCorridas2 (json=true)."""
    _ensure_session(http)
    data: Dict[str, Any] = {"json": True}
    if page > 1:
        data["page"] = page

    r = http.post(BASE_URL + "/solicitacao/historicoCorridas2", data=data, timeout=60)
    payload = _parse_json_response(r)
    render = payload.get("render") or ""
    if not render:
        raise RuntimeError("Resposta vazia ao listar corridas.")

    corridas, meta = _parse_grid_render(render)
    if apenas_ativos_mapa:
        corridas_filtradas = [c for c in corridas if c.get("ativo_mapa")]
    else:
        corridas_filtradas = corridas

    if incluir_coordenadas:
        alvos = [c for c in corridas_filtradas if c.get("ativo_mapa")][:max_coordenadas]
        coords = _buscar_coordenadas_lote(http, [c["id"] for c in alvos])
        for c in corridas_filtradas:
            extra = coords.get(c["id"])
            if extra:
                c.update(extra)

    return {
        "sucesso": True,
        "corridas": corridas_filtradas,
        "meta": meta,
        "filtro": payload.get("HistoricoFilterForm"),
    }


def _buscar_coordenadas_lote(http: requests.Session, ids: List[str]) -> Dict[str, Dict[str, Any]]:
    if not ids:
        return {}
    out: Dict[str, Dict[str, Any]] = {}

    def _fetch(os_id: str) -> Tuple[str, Dict[str, Any]]:
        pos = obter_posicao_corrida(http, os_id)
        lat_p, lng_p = _normalizar_coord_br(pos.get("lat_partida"), pos.get("lng_partida"))
        lat_m, lng_m = _normalizar_coord_br(pos.get("lat_taxista"), pos.get("lng_taxista"))
        return os_id, {
            "lat_partida": lat_p,
            "lng_partida": lng_p,
            "lat_motorista": lat_m,
            "lng_motorista": lng_m,
            "status_codigo": pos.get("status_solicitacao"),
        }

    workers = min(8, len(ids))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch, os_id): os_id for os_id in ids}
        for fut in as_completed(futures):
            try:
                os_id, data = fut.result()
                out[os_id] = data
            except Exception as exc:
                log.warning("coordenadas %s: %s", futures[fut], exc)
    return out


def obter_posicao_corrida(http: requests.Session, os_id: str) -> Dict[str, Any]:
    """Posição em tempo quase real — GET /solicitacao/detalhesDaCorrida."""
    r = http.get(
        BASE_URL + "/solicitacao/detalhesDaCorrida",
        params={"id": os_id},
        timeout=30,
    )
    data = _parse_json_response(r)
    if not data:
        raise RuntimeError(f"Detalhes de posição indisponíveis para OS {os_id}.")
    return data


def _parse_detalhes_html(html: str, os_id: str) -> Dict[str, Any]:
    det: Dict[str, Any] = {"id": os_id}

    titulo = re.search(r"<h1[^>]*>.*?(\d{6,}).*?</h1>", html, re.S)
    if titulo:
        det["os"] = titulo.group(1)

    status_m = re.search(r'class="status[^"]*"><p>([^<]+)', html)
    if status_m:
        det["status"] = status_m.group(1).strip()

    link_rastreio = re.search(
        r'href="(https://cloud\.machine\.global/solicitacao/acompanhar/[^"]+)"',
        html,
    )
    if link_rastreio:
        det["link_rastreio"] = link_rastreio.group(1)

    for m in re.finditer(r"<label>([^<]+)</label>([^<]*(?:<[^/][^>]*>[^<]*)*)", html):
        label = m.group(1).strip()
        valor = re.sub(r"<[^>]+>", " ", m.group(2))
        valor = re.sub(r"\s+", " ", valor).strip()
        if not valor:
            continue
        chave = (
            label.lower()
            .replace(" ", "_")
            .replace("(", "")
            .replace(")", "")
            .replace("/", "_")
            .replace("ã", "a")
            .replace("ç", "c")
            .replace("é", "e")
            .replace("ó", "o")
            .replace("á", "a")
            .replace("í", "i")
            .replace("ú", "u")
        )
        chave = re.sub(r"[^a-z0-9_]+", "_", chave).strip("_")
        det[chave] = valor

    return det


def obter_detalhe_corrida(http: requests.Session, os_id: str) -> Dict[str, Any]:
    """Detalhe completo: painel HTML + coordenadas JSON."""
    pos_task = obter_posicao_corrida(http, os_id)
    r = http.get(
        BASE_URL + "/solicitacao/detalhesCorridas",
        params={"tipo": 3, "nova": 1, "tipoHistorico": "corrida", "id": os_id},
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Falha ao obter detalhes da OS {os_id}.")

    detalhes = _parse_detalhes_html(r.text, os_id)
    pos = pos_task
    intercorrencias = _parse_intercorrencias(r.text)
    lat_p, lng_p = _normalizar_coord_br(pos.get("lat_partida"), pos.get("lng_partida"))
    lat_m, lng_m = _normalizar_coord_br(pos.get("lat_taxista"), pos.get("lng_taxista"))

    status_codigo = pos.get("status_solicitacao")
    status_texto = _STATUS_TEXTO.get(str(status_codigo), detalhes.get("status"))

    return {
        "sucesso": True,
        "id": os_id,
        "os": detalhes.get("os") or os_id,
        "status": status_texto or detalhes.get("status"),
        "status_codigo": status_codigo,
        "link_rastreio": detalhes.get("link_rastreio"),
        "tem_alerta": bool(intercorrencias),
        "intercorrencias": intercorrencias,
        "informacoes": detalhes,
        "posicao": {
            "lat_partida": lat_p,
            "lng_partida": lng_p,
            "lat_motorista": lat_m,
            "lng_motorista": lng_m,
            "trajeto": pos.get("array_posicao") or [],
            "paradas": pos.get("array_parada") or [],
        },
        "passageiro": {
            "nome": pos.get("nome_passageiro") or detalhes.get("nome_do_passageiro"),
            "telefone": detalhes.get("telefone_do_passageiro"),
        },
        "motorista": {
            "nome": detalhes.get("motorista") or pos.get("nome_taxista"),
            "telefone": detalhes.get("telefone_do_motorista"),
            "placa": pos.get("placa") or detalhes.get("placa"),
            "modelo": pos.get("modelo") or detalhes.get("modelo"),
            "cor": pos.get("cor") or detalhes.get("cor"),
        },
        "endereco_partida": pos.get("endereco_partida") or detalhes.get("local_de_embarque"),
        "destino": detalhes.get("destino_informado"),
    }
