"""
Cliente HTTP para tarifa dinâmica do painel TaxiMachine (/tarifaCategoria/dinamica).

Reutiliza sessão cookie (PHPSESSID) compartilhada com machine_notificacao_http.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from machine_notificacao_http import BASE_URL, _parse_json_response


def _area_resumo(area: Dict[str, Any], incluir_vertices: bool) -> Dict[str, Any]:
    resumo = {
        "area_id": area.get("area_id"),
        "fator_id": area.get("fator_id"),
        "nome": area.get("nome"),
        "ativo": area.get("ativo") in (1, "1", True),
        "fator": area.get("fator"),
        "tipo_calculo": area.get("tipo_calculo"),
        "tipo_fator": area.get("tipo_fator"),
        "valor_adicional": area.get("valor_adicional") or None,
        "bandeira_id": area.get("bandeira_id"),
        "cor_preenchimento": area.get("cor_preenchimento"),
    }
    if incluir_vertices:
        resumo["vertices"] = area.get("vertices") or []
        resumo["lat_minima"] = area.get("lat_minima")
        resumo["lat_maxima"] = area.get("lat_maxima")
        resumo["lng_minima"] = area.get("lng_minima")
        resumo["lng_maxima"] = area.get("lng_maxima")
    return resumo


def listar_areas(
    http: requests.Session,
    bandeira_id: str,
    incluir_vertices: bool = False,
    apenas_ativas: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Lista áreas de dinâmica manual (mapa) via GET interno dinamicaArea.
    """
    if not bandeira_id:
        raise RuntimeError("bandeira_id é obrigatório.")

    r = http.get(
        BASE_URL + "/tarifaCategoria/dinamicaArea",
        params={"bandeira_id": bandeira_id},
        timeout=60,
    )
    if "LoginForm" in r.text and "dinamica" not in r.text[:2000].lower():
        raise RuntimeError("Sessão expirada — faça login novamente.")

    try:
        data = r.json()
    except Exception as exc:
        raise RuntimeError(
            f"Resposta inválida de dinamicaArea (HTTP {r.status_code}): {(r.text or '')[:300]}"
        ) from exc

    raw_areas: List[Dict[str, Any]] = data.get("areas") or []
    if apenas_ativas is True:
        raw_areas = [a for a in raw_areas if str(a.get("ativo")) == "1"]
    elif apenas_ativas is False:
        raw_areas = [a for a in raw_areas if str(a.get("ativo")) != "1"]

    areas = [_area_resumo(a, incluir_vertices) for a in raw_areas]
    global_d = data.get("global")
    global_resumo = None
    if global_d:
        global_resumo = {
            "fator_id": global_d.get("fator_id"),
            "fator": global_d.get("fator"),
            "ativo": global_d.get("ativo") in (1, "1", True),
            "tipo_calculo": global_d.get("tipo_calculo"),
            "tipo_fator": global_d.get("tipo_fator"),
            "valor_adicional": global_d.get("valor_adicional"),
            "bandeira_id": global_d.get("bandeira_id"),
        }

    total_ativas = sum(1 for a in (data.get("areas") or []) if str(a.get("ativo")) == "1")

    return {
        "sucesso": True,
        "bandeira_id": str(bandeira_id),
        "total": len(data.get("areas") or []),
        "total_retornado": len(areas),
        "total_ativas": total_ativas,
        "total_inativas": len(data.get("areas") or []) - total_ativas,
        "global": global_resumo,
        "areas": areas,
    }


def alterar_ativo_area(
    http: requests.Session,
    bandeira_id: str,
    fator_id: str,
    area_id: str,
    ativo: bool,
) -> Dict[str, Any]:
    r = http.post(
        BASE_URL + "/tarifaCategoria/ativarFator",
        data={
            "fator_id": fator_id,
            "area_id": area_id,
            "ativo": 1 if ativo else 0,
            "bandeira_id": bandeira_id,
        },
        timeout=30,
    )
    ok = (r.text or "").strip().lower() == "true"
    return {
        "sucesso": ok,
        "fator_id": fator_id,
        "area_id": area_id,
        "ativo": ativo,
        "resposta_bruta": (r.text or "").strip(),
    }
