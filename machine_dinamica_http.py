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


def _vertices_para_string(vertices: List[Any]) -> str:
    """
    Converte lista de vértices para o formato do painel: 'lat,lng;lat,lng;'.
    Aceita dicts {'lat','lng'} ou tuplas (lat, lng).
    """
    partes: List[str] = []
    for v in vertices:
        if isinstance(v, dict):
            lat, lng = v.get("lat"), v.get("lng")
        elif isinstance(v, (list, tuple)) and len(v) >= 2:
            lat, lng = v[0], v[1]
        else:
            raise RuntimeError("Cada vértice deve ser {'lat','lng'} ou (lat, lng).")
        if lat is None or lng is None:
            raise RuntimeError("Vértice inválido: lat e lng são obrigatórios.")
        partes.append(f"{lat},{lng}")
    if len(partes) < 3:
        raise RuntimeError("Polígono precisa de pelo menos 3 vértices.")
    return ";".join(partes) + ";"


def _parse_area_resposta(data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "area_id": data.get("area_id"),
        "fator_id": data.get("fator_id"),
        "nome": data.get("nome"),
        "ativo": data.get("ativo") in (1, "1", True),
        "fator": data.get("fator"),
        "tipo_calculo": data.get("tipo_calculo"),
        "tipo_fator": data.get("tipo_fator"),
        "valor_adicional": data.get("valor_adicional"),
        "bandeira_id": data.get("bandeira_id"),
        "cor_preenchimento": data.get("cor_preenchimento"),
        "vertices": data.get("vertices") or [],
        "lat_minima": data.get("lat_minima"),
        "lat_maxima": data.get("lat_maxima"),
        "lng_minima": data.get("lng_minima"),
        "lng_maxima": data.get("lng_maxima"),
    }


def _extrair_erro_api(r: requests.Response) -> Optional[str]:
    try:
        body = r.json()
        if isinstance(body, dict):
            erros = body.get("errors")
            if erros:
                return erros[0] if isinstance(erros, list) else str(erros)
            if body.get("success") is False:
                return str(body)
    except Exception:
        pass
    texto = (r.text or "").strip()
    return texto[:300] if texto else None


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
    if not ok:
        erro = _extrair_erro_api(r)
        if erro:
            raise RuntimeError(erro)
    return {
        "sucesso": ok,
        "fator_id": fator_id,
        "area_id": area_id,
        "ativo": ativo,
        "resposta_bruta": (r.text or "").strip(),
    }


def editar_fator_area(
    http: requests.Session,
    bandeira_id: str,
    fator_id: str,
    area_id: str,
    fator: str,
    tipo_calculo: str = "M",
    valor_adicional: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Altera apenas o multiplicador (ou valor adicional) de uma área existente.

    Atenção: o painel gera um novo fator_id a cada edição — use o retorno
    desta função nas próximas chamadas de ativar/desativar/editar.
    """
    eh_multiplicador = tipo_calculo == "M"
    payload: Dict[str, Any] = {
        "fator_id": fator_id,
        "area_id": area_id,
        "fator_area": fator if eh_multiplicador else "",
        "tipo_calculo": tipo_calculo,
        "area_alterada": "false",
        "bandeira_id": bandeira_id,
        "valor_adicional": valor_adicional if not eh_multiplicador else "",
    }
    r = http.post(
        BASE_URL + "/tarifaCategoria/editarFatorDinamica",
        data=payload,
        timeout=30,
    )
    if not r.ok:
        erro = _extrair_erro_api(r)
        raise RuntimeError(erro or f"HTTP {r.status_code}")

    try:
        data = r.json()
    except Exception as exc:
        raise RuntimeError(f"Resposta inválida ao editar fator: {(r.text or '')[:300]}") from exc

    if isinstance(data, dict) and data.get("success") is False:
        raise RuntimeError(_extrair_erro_api(r) or "Erro ao editar fator.")

    area = _parse_area_resposta(data)
    return {"sucesso": True, "area": area, "fator_id_novo": area.get("fator_id")}


def editar_area(
    http: requests.Session,
    bandeira_id: str,
    fator_id: str,
    area_id: str,
    nome_area: str,
    fator: str,
    vertices: List[Any],
    tipo_calculo: str = "M",
    tipo_fator: str = "P",
    cor_preenchimento: str = "#ffa500",
    area_alterada: bool = True,
    valor_adicional: Optional[str] = None,
) -> Dict[str, Any]:
    """Edita área completa (nome, fator e/ou polígono)."""
    eh_multiplicador = tipo_calculo == "M"
    payload: Dict[str, Any] = {
        "fator_id": fator_id,
        "bandeira_id": bandeira_id,
        "area_id": area_id,
        "nome_area": nome_area,
        "tipo_fator": tipo_fator,
        "fator_area": fator if eh_multiplicador else "",
        "valor_adicional": valor_adicional if not eh_multiplicador else "",
        "vertices": _vertices_para_string(vertices),
        "cor_preenchimento": cor_preenchimento,
        "area_alterada": "true" if area_alterada else "false",
        "tipo_calculo": tipo_calculo,
    }
    r = http.post(
        BASE_URL + "/tarifaCategoria/editarFatorDinamica",
        data=payload,
        timeout=30,
    )
    if not r.ok:
        erro = _extrair_erro_api(r)
        raise RuntimeError(erro or f"HTTP {r.status_code}")

    try:
        data = r.json()
    except Exception as exc:
        raise RuntimeError(f"Resposta inválida ao editar área: {(r.text or '')[:300]}") from exc

    if isinstance(data, dict) and data.get("success") is False:
        raise RuntimeError(_extrair_erro_api(r) or "Erro ao editar área.")

    area = _parse_area_resposta(data)
    return {"sucesso": True, "area": area, "fator_id_novo": area.get("fator_id")}


def criar_area(
    http: requests.Session,
    bandeira_id: str,
    nome_area: str,
    fator: str,
    vertices: List[Any],
    tipo_calculo: str = "M",
    tipo_fator: str = "P",
    cor_preenchimento: str = "#ffa500",
    valor_adicional: Optional[str] = None,
) -> Dict[str, Any]:
    """Cria nova área de dinâmica manual com polígono."""
    eh_multiplicador = tipo_calculo == "M"
    payload: Dict[str, Any] = {
        "nome_area": nome_area,
        "fator_area": fator if eh_multiplicador else "",
        "valor_adicional": valor_adicional if not eh_multiplicador else "",
        "bandeira_id": bandeira_id,
        "tipo_fator": tipo_fator,
        "vertices": _vertices_para_string(vertices),
        "cor_preenchimento": cor_preenchimento,
        "tipo_calculo": tipo_calculo,
    }
    r = http.post(
        BASE_URL + "/tarifaCategoria/criarAreaDinamica",
        data=payload,
        timeout=30,
    )
    if not r.ok:
        erro = _extrair_erro_api(r)
        raise RuntimeError(erro or f"HTTP {r.status_code}")

    try:
        data = r.json()
    except Exception as exc:
        raise RuntimeError(f"Resposta inválida ao criar área: {(r.text or '')[:300]}") from exc

    area = _parse_area_resposta(data)
    return {"sucesso": True, "area": area}


def apagar_area(
    http: requests.Session,
    bandeira_id: str,
    fator_id: str,
    area_id: str,
) -> Dict[str, Any]:
    r = http.post(
        BASE_URL + "/tarifaCategoria/apagarFatorDinamica",
        data={
            "fator_id": fator_id,
            "area_id": area_id,
            "bandeira_id": bandeira_id,
        },
        timeout=30,
    )
    ok = (r.text or "").strip().lower() == "true"
    if not ok:
        erro = _extrair_erro_api(r)
        if erro:
            raise RuntimeError(erro)
    return {
        "sucesso": ok,
        "fator_id": fator_id,
        "area_id": area_id,
        "resposta_bruta": (r.text or "").strip(),
    }
