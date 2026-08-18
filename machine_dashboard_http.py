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


_IMG_TAG_RE = re.compile(r"<img[^>]*>", re.I)


def _tag_eh_celular(tag: str) -> bool:
    return bool(
        re.search(
            r"celular|smartphone|mobile|phone|app[-_]?pass|passageiro|device|android|iphone|icone-app",
            tag,
            re.I,
        )
    )


def _parse_coluna_indicadores(html: str, row_html: str = "") -> Dict[str, Any]:
    """
    Coluna de indicadores: hora + ícone de app (sempre) + triângulo de alerta (opcional).
    Na Machine o alerta costuma ser um <img> extra (não o celular).
    """
    blob = html or ""
    imgs = _IMG_TAG_RE.findall(blob)
    non_phone = [t for t in imgs if not _tag_eh_celular(t)]
    tem_alerta = len(non_phone) > 0

    if not tem_alerta:
        tem_alerta = bool(
            re.search(
                r"(?:fa-exclamation|glyphicon-warning|icon-alerta|icon_alerta|intercorr|class=\"[^\"]*alerta[^\"]*\")",
                blob,
                re.I,
            )
        )
    if not tem_alerta and row_html:
        tem_alerta = bool(
            re.search(
                r"(?:intercorr|icon-alerta|icon_alerta|alerta\.png|exclamation-triangle|triangulo)",
                row_html,
                re.I,
            )
        )

    hora_m = re.search(r"\b(\d{2}:\d{2}:\d{2})\b", blob)
    return {
        "tem_alerta": tem_alerta,
        "hora": hora_m.group(1) if hora_m else "",
    }


def _coord_valida_br(lat: Any, lng: Any) -> bool:
    try:
        la, ln = float(lat), float(lng)
    except (TypeError, ValueError):
        return False
    if la == 0 or ln == 0:
        return False
    if abs(la) < 0.05 and abs(ln) < 0.05:
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
        r'class="[^"]*(?:box-alert|alert-warning|alert-danger|alert-info|alerta|mensagem-alerta|intercorr|aviso)[^"]*"[^>]*>(.*?)</(?:div|section|ul)>',
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
    # Machine: parágrafos com texto de intercorrência (ex.: "Motorista demorou...")
    for m in re.finditer(r"<p[^>]*>([^<]{12,300})</p>", html, re.S):
        t = _text(m.group(1))
        if not t or len(t) < 12:
            continue
        if re.search(r"demorou|atraso|intercorr|nao chegou|não chegou|aceite", t, re.I):
            msgs.append(t)
    return list(dict.fromkeys(msgs))


def _parse_linha_corrida(row_html: str) -> Optional[Dict[str, Any]]:
    os_id = re.search(r'class="identificador">(\d+)', row_html)
    if not os_id:
        return None
    cols = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S)
    if len(cols) < 9:
        return None

    indicadores = _parse_coluna_indicadores(cols[1] if len(cols) > 1 else "", row_html)

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
    if not total_m:
        total_m = re.search(r"de\s+([\d\.]+)\s+resultados?", render)
    if total_m:
        total = int(total_m.group(1).replace(".", ""))
    paginas_link = len(set(re.findall(r"page=(\d+)", render)))
    if total and corridas:
        paginas = max(1, (total + len(corridas) - 1) // len(corridas))
    else:
        paginas = max(paginas_link, 1)
    meta = {"summary": summary, "total": total, "paginas": paginas, "pagina_tamanho": len(corridas) or None}
    return corridas, meta


def _enriquecer_alertas_via_detalhe(
    http: requests.Session, corridas: List[Dict[str, Any]], max_rows: int = 50
) -> None:
    """Fallback: confirma alertas via HTML de detalhe quando a grade não traz o ícone."""
    alvos = [c for c in corridas if not c.get("tem_alerta")][:max_rows]
    if not alvos:
        return

    def _fetch(c: Dict[str, Any]) -> Tuple[str, bool]:
        os_id = str(c["id"])
        try:
            r = http.get(
                BASE_URL + "/solicitacao/detalhesCorridas",
                params={"tipo": 3, "nova": 1, "tipoHistorico": "corrida", "id": os_id},
                timeout=45,
            )
            if r.status_code != 200:
                return os_id, False
            return os_id, bool(_parse_intercorrencias(r.text))
        except Exception as exc:
            log.warning("alerta detalhe %s: %s", os_id, exc)
            return os_id, False

    workers = min(6, len(alvos))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for os_id, tem in pool.map(_fetch, alvos):
            if not tem:
                continue
            for c in corridas:
                if str(c["id"]) == os_id:
                    c["tem_alerta"] = True
                    break


def _fetch_corridas_pagina(http: requests.Session, page: int = 1) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Any]:
    """Busca uma página da grade historicoCorridas2."""
    _ensure_session(http)
    url = BASE_URL + "/solicitacao/historicoCorridas2"
    if page > 1:
        url += f"?page={page}"

    r = http.post(url, data={"json": "true"}, timeout=60)
    payload = _parse_json_response(r)
    render = payload.get("render") or ""
    if not render:
        raise RuntimeError("Resposta vazia ao listar corridas.")
    corridas, meta = _parse_grid_render(render)
    return corridas, meta, payload.get("HistoricoFilterForm")


def _aplicar_pos_processamento_corridas(
    http: requests.Session,
    corridas: List[Dict[str, Any]],
    *,
    incluir_coordenadas: bool = False,
    apenas_ativos_mapa: bool = False,
    max_coordenadas: int = 40,
    enriquecer_alertas: bool = False,
) -> List[Dict[str, Any]]:
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

    if enriquecer_alertas and corridas_filtradas:
        _enriquecer_alertas_via_detalhe(
            http,
            corridas_filtradas,
            max_rows=min(120, len(corridas_filtradas)),
        )

    return corridas_filtradas


def listar_corridas(
    http: requests.Session,
    *,
    page: int = 1,
    incluir_coordenadas: bool = False,
    apenas_ativos_mapa: bool = False,
    max_coordenadas: int = 40,
    enriquecer_alertas: bool = False,
) -> Dict[str, Any]:
    """Lista corridas da grade via POST /solicitacao/historicoCorridas2 (json=true)."""
    corridas, meta, filtro = _fetch_corridas_pagina(http, page)
    corridas_filtradas = _aplicar_pos_processamento_corridas(
        http,
        corridas,
        incluir_coordenadas=incluir_coordenadas,
        apenas_ativos_mapa=apenas_ativos_mapa,
        max_coordenadas=max_coordenadas,
        enriquecer_alertas=enriquecer_alertas,
    )

    return {
        "sucesso": True,
        "corridas": corridas_filtradas,
        "meta": meta,
        "filtro": filtro,
    }


def listar_corridas_todas(
    http: requests.Session,
    *,
    incluir_coordenadas: bool = False,
    apenas_ativos_mapa: bool = False,
    max_coordenadas: int = 40,
    enriquecer_alertas: bool = False,
    max_paginas: int = 30,
) -> Dict[str, Any]:
    """Percorre todas as páginas da grade e retorna a lista completa."""
    primeira, meta, filtro = _fetch_corridas_pagina(http, 1)
    tamanho_pagina = len(primeira) or int(meta.get("pagina_tamanho") or 25)
    total_esperado = meta.get("total")
    paginas_meta = meta.get("paginas")

    por_id: Dict[str, Dict[str, Any]] = {str(c["id"]): c for c in primeira if c.get("id")}
    paginas_consultadas = 1

    if paginas_meta:
        limite_paginas = min(max_paginas, int(paginas_meta))
    elif total_esperado and tamanho_pagina:
        limite_paginas = min(
            max_paginas,
            max(1, (int(total_esperado) + tamanho_pagina - 1) // tamanho_pagina),
        )
    else:
        limite_paginas = max_paginas

    pagina = 2
    while pagina <= limite_paginas:
        if total_esperado and len(por_id) >= int(total_esperado):
            break
        lote, meta_p, _ = _fetch_corridas_pagina(http, pagina)
        if not lote:
            break
        antes = len(por_id)
        for c in lote:
            cid = str(c.get("id") or "")
            if cid:
                por_id[cid] = c
        paginas_consultadas = pagina
        if meta_p.get("paginas"):
            meta["paginas"] = max(int(meta.get("paginas") or 1), int(meta_p["paginas"]))
        if len(por_id) == antes:
            break
        if total_esperado and len(por_id) >= int(total_esperado):
            break
        if len(lote) < tamanho_pagina:
            break
        pagina += 1

    todas = list(por_id.values())
    meta = {
        **meta,
        "paginas_consultadas": paginas_consultadas,
        "total_listado": len(todas),
    }

    corridas_filtradas = _aplicar_pos_processamento_corridas(
        http,
        todas,
        incluir_coordenadas=incluir_coordenadas,
        apenas_ativos_mapa=apenas_ativos_mapa,
        max_coordenadas=max_coordenadas,
        enriquecer_alertas=enriquecer_alertas,
    )

    return {
        "sucesso": True,
        "corridas": corridas_filtradas,
        "meta": meta,
        "filtro": filtro,
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


def _status_bloqueia_rastreio(status: Optional[str]) -> bool:
    s = (status or "").lower()
    return "cancel" in s or "finaliz" in s


def _montar_payload_alerta_monitor(
    *,
    cidade_id: str,
    cidade_nome: str,
    empresa_id: str,
    empresa_nome: str,
    bandeira_nome: str,
    linha: Dict[str, Any],
    detalhe: Dict[str, Any],
) -> Dict[str, Any]:
    intercorrencias = detalhe.get("intercorrencias") or []
    if not intercorrencias and linha.get("tem_alerta"):
        intercorrencias = ["Alerta operacional na grade"]
    status = str(detalhe.get("status") or linha.get("status") or "")
    link = detalhe.get("link_rastreio")
    passageiro = detalhe.get("passageiro") or {}
    motorista = detalhe.get("motorista") or {}
    return {
        "cidade_id": cidade_id,
        "cidade_nome": cidade_nome,
        "empresa_id": empresa_id,
        "empresa_nome": empresa_nome,
        "bandeira": bandeira_nome or linha.get("empresa") or "",
        "alerta": intercorrencias[0] if intercorrencias else "",
        "alertas": intercorrencias,
        "status_corrida": status,
        "numero_os": str(detalhe.get("os") or linha.get("os") or linha.get("id") or ""),
        "link_rastreio": None if _status_bloqueia_rastreio(status) else link,
        "passageiro": {
            "nome": passageiro.get("nome") or linha.get("passageiro") or "",
            "telefone": passageiro.get("telefone") or "",
        },
        "motorista": {
            "nome": motorista.get("nome") or linha.get("motorista") or "",
            "telefone": motorista.get("telefone") or "",
        },
    }


def monitor_alertas_cidades(
    http: requests.Session,
    cidades: List[Dict[str, Any]],
    *,
    horas: float = 4,
    max_detalhes_por_cidade: int = 20,
) -> Dict[str, Any]:
    """
    Para cada cidade (bandeira), aplica filtro, lista corridas com alerta e enriquece detalhe.
    Reutiliza a mesma sessão HTTP (login único por empresa).
    """
    bandeira_nomes = {b["id"]: b["nome"] for b in obter_bandeiras_historico(http)}
    alertas: List[Dict[str, Any]] = []
    erros: List[Dict[str, str]] = []

    for cidade in cidades:
        cidade_id = str(cidade.get("cidade_id") or "")
        bandeira_id = str(cidade.get("bandeira_id") or "")
        if not bandeira_id:
            erros.append({"cidade_id": cidade_id, "erro": "bandeira_id ausente"})
            continue
        try:
            aplicar_filtro_corridas(http, bandeira_id=bandeira_id, horas=horas)
            lista = listar_corridas_todas(
                http,
                incluir_coordenadas=False,
                enriquecer_alertas=True,
            )
            com_alerta = [c for c in lista.get("corridas") or [] if c.get("tem_alerta")]
            for linha in com_alerta[:max_detalhes_por_cidade]:
                os_id = str(linha.get("id") or "")
                if not os_id:
                    continue
                det = obter_detalhe_corrida(http, os_id)
                alertas.append(
                    _montar_payload_alerta_monitor(
                        cidade_id=cidade_id,
                        cidade_nome=str(cidade.get("cidade_nome") or ""),
                        empresa_id=str(cidade.get("empresa_id") or ""),
                        empresa_nome=str(cidade.get("empresa_nome") or ""),
                        bandeira_nome=bandeira_nomes.get(bandeira_id, ""),
                        linha=linha,
                        detalhe=det,
                    )
                )
        except Exception as exc:
            log.warning("monitor alertas cidade %s: %s", cidade_id, exc)
            erros.append({"cidade_id": cidade_id, "erro": str(exc)})

    return {"sucesso": True, "alertas": alertas, "erros": erros, "horas": horas}
