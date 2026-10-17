#!/usr/bin/env python3
"""GET /taxista/update/{id}: lista selects e candidatos ao campo Relação (sem imprimir senhas)."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auto_2fa import gerar_codigo, obter_chave
from machine_notificacao_http import BASE_URL, get_session, login_painel
from machine_taxista_http import _TaxistaFormParser, descobrir_campo_relacao


def main() -> int:
    email = os.environ.get("SMOKE_CIDADE_EMAIL", "").strip()
    senha = os.environ.get("SMOKE_CIDADE_SENHA", "").strip()
    totp = os.environ.get("SMOKE_CIDADE_TOTP", "").replace(" ", "")
    taxista_id = os.environ.get("PROBE_TAXISTA_ID", "772770").strip()
    if not email or not senha:
        print("Defina SMOKE_CIDADE_EMAIL e SMOKE_CIDADE_SENHA")
        return 1

    login = login_painel(
        email=email,
        senha=senha,
        chave_secreta=totp or obter_chave(email) or None,
        gerar_codigo_fn=gerar_codigo,
    )
    token = login["session_token"]
    http = get_session(token)
    if not http:
        print("sessão não encontrada após login")
        return 2

    url = f"{BASE_URL}/taxista/update/{taxista_id}"
    r = http.get(url, timeout=120)
    text = r.text or ""
    print("GET", url, "status", r.status_code, "len", len(text))
    if "LoginForm" in text and "Taxista[" not in text:
        print("sessão inválida")
        return 2

    selects = _TaxistaFormParser.list_selects(text)
    print(f"\n{len(selects)} selects no formulário:")
    for sel in selects:
        print("\n---", sel["name"], "---")
        for opt in sel["options"]:
            mark = "*" if opt.get("selected") else " "
            print(f"  {mark} value={opt['value']!r} text={opt['text']!r}")

    try:
        campo, mapping = descobrir_campo_relacao(text)
        print("\nDescoberto automaticamente:")
        print(json.dumps({"campo": campo, "valores": mapping}, ensure_ascii=False, indent=2))
    except RuntimeError as e:
        print("\nAuto-descoberta falhou:", e)
        return 3

    taxista_names = sorted(
        set(
            n
            for s in selects
            for n in [s["name"]]
            if "Taxista" in n
        )
    )
    print(f"\nNomes Taxista[...] (selects): {len(taxista_names)}")
    for n in taxista_names[:30]:
        print(n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
