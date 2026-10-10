#!/usr/bin/env python3
"""Descobre URLs/campos do export de motoristas em /taxista/index (não imprime senhas)."""
from __future__ import annotations

import json
import os
import re
import sys

# repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from machine_notificacao_http import BASE_URL, _new_session
from machine_notificacao_http import login_painel
from auto_2fa import gerar_codigo, obter_chave


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
        chave_secreta=totp or None,
        gerar_codigo_fn=gerar_codigo,
    )
    token = login["session_token"]
    _, http = _new_session(email)
    from machine_notificacao_http import _sessions, _sessions_lock

    with _sessions_lock:
        http = _sessions[token]["http"]

    r = http.get(BASE_URL + "/taxista/index", timeout=60)
    text = r.text or ""
    print("GET /taxista/index", r.status_code, "len", len(text))
    if "site/login" in (r.url or "") or "LoginForm" in text[:8000]:
        print("sessão inválida")
        return 2

    for pat in (
        r"solicitarExportar[^\s\"']+",
        r"verificarStatus[^\s\"']+",
        r"TaxistaFilterForm[^\s\"']*",
        r"/taxista/[a-zA-Z]+",
        r"exportarRelatorio[^\s\"']+",
        r"relatorioTaxista[^\s\"']+",
    ):
        found = sorted(set(re.findall(pat, text, re.I)))
        if found:
            print(f"\n-- {pat} ({len(found)}) --")
            for x in found[:40]:
                print(x)

    # inputs do filtro
    names = sorted(set(re.findall(r'name="(TaxistaFilterForm\[[^\]]+\])"', text)))
    print(f"\nTaxistaFilterForm fields: {len(names)}")
    for n in names[:60]:
        print(n)
    if len(names) > 60:
        print("...")

    # status tabs
    for m in re.finditer(r"status[^\"']{0,40}ativo", text, re.I):
        snippet = text[max(0, m.start() - 40) : m.end() + 40]
        print("snippet:", re.sub(r"\s+", " ", snippet)[:120])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
