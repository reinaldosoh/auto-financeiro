#!/usr/bin/env python3
"""Probe /categoria/tarifa e /categoria/update (sem imprimir senhas)."""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auto_2fa import gerar_codigo, obter_chave
from machine_categoria_http import (
    FIELD_EXIBIR,
    FIELD_ORDEM,
    carregar_form_categoria,
    listar_categorias_tarifa,
)
from machine_notificacao_http import BASE_URL, get_session, login_painel


def main() -> int:
    email = os.environ.get("SMOKE_CIDADE_EMAIL", "").strip()
    senha = os.environ.get("SMOKE_CIDADE_SENHA", "").strip()
    totp = os.environ.get("SMOKE_CIDADE_TOTP", "").replace(" ", "")
    if not email or not senha:
        print("Defina SMOKE_CIDADE_EMAIL e SMOKE_CIDADE_SENHA")
        return 1

    login = login_painel(
        email=email,
        senha=senha,
        chave_secreta=totp or obter_chave(email) or None,
        gerar_codigo_fn=gerar_codigo,
    )
    http = get_session(login["session_token"])
    if not http:
        print("sessão inválida")
        return 2

    cats = listar_categorias_tarifa(http)
    print(f"listar: {len(cats)} categorias")
    print(json.dumps(cats, ensure_ascii=False, indent=2))

    probe_id = os.environ.get("PROBE_CATEGORIA_ID", "").strip()
    if not probe_id and cats:
        for c in cats:
            if "maçaneta" in c["nome"].lower() or "macaneta" in c["nome"].lower():
                probe_id = str(c["categoria_id"])
                break
    if probe_id:
        _, html = carregar_form_categoria(http, int(probe_id))
        print(f"\nupdate/{probe_id} campos ordem/exibir:")
        print("  ordem field:", FIELD_ORDEM)
        print("  exibir field:", FIELD_EXIBIR)
        for pat in (FIELD_ORDEM, FIELD_EXIBIR, "Categoria[nome]"):
            if pat in html:
                print(f"  ok {pat}")
        m = re.search(r'onclick="reordenar\(this\)"', html)
        print("  reordenar UI:", "sim" if m else "só em /categoria/tarifa")

    r = http.get(BASE_URL + "/categoria/tarifa", timeout=120)
    for m in re.finditer(r"/categoria/[a-zA-Z]+", r.text or ""):
        pass
    urls = sorted(set(re.findall(r"/categoria/[a-zA-Z]+", r.text or "")))
    print("\npaths categoria:", urls[:20])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
