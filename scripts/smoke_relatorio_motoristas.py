#!/usr/bin/env python3
"""Smoke POST /relatorio/motoristas/solicitar na VPS (credenciais via env, sem imprimir segredos)."""
from __future__ import annotations

import csv
import io
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

BASE_VPS = os.environ.get(
    "MACHINE_API_BASE", "https://reinaldo-automachine.sw5bxa.easypanel.host"
).rstrip("/")


def load_api_key() -> str:
    k = os.environ.get("MACHINE_API_KEY", "").strip()
    if k:
        return k
    env_path = pathlib.Path(
        os.environ.get(
            "KEY_FILE",
            "/Users/reinaldofc/Library/Application Support/Cursor/AgentStores/"
            "cursor_agent_stores/u474527586/files/machine-api-rollout-vps.env",
        )
    )
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("MACHINE_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("MACHINE_API_KEY ausente")


def http_json(method: str, url: str, key: str, body: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-API-Key", key)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, {"raw": raw[:300]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw[:300]}


def main() -> int:
    email = os.environ.get("SMOKE_CIDADE_EMAIL", "").strip()
    senha = os.environ.get("SMOKE_CIDADE_SENHA", "").strip()
    totp = os.environ.get("SMOKE_CIDADE_TOTP", "").replace(" ", "")
    if not email or not senha:
        print("Defina SMOKE_CIDADE_EMAIL e SMOKE_CIDADE_SENHA")
        return 1

    api_key = load_api_key()
    corpo: dict = {
        "email": email,
        "senha": senha,
        "aguardar_seg": int(os.environ.get("SMOKE_AGUARDAR_SEG", "180")),
    }
    if totp:
        corpo["chave_secreta"] = totp

    code, payload = http_json("POST", f"{BASE_VPS}/relatorio/motoristas/solicitar", api_key, corpo)
    detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else payload
    sucesso = detail.get("sucesso") if isinstance(detail, dict) else payload.get("sucesso")
    url = (detail or payload).get("url")
    print(f"VPS motoristas/solicitar: HTTP {code} sucesso={sucesso} pronto={bool(url)}")
    if not url:
        msg = (detail or payload).get("mensagem") or str(payload)[:200]
        print(f"mensagem: {msg}")
        return 2 if code != 200 else 3

    with urllib.request.urlopen(url, timeout=120) as resp:
        head = resp.read(65536).decode("utf-8", errors="replace")
    lines = head.splitlines()
    if not lines or "Id" not in lines[0]:
        print("CSV sem coluna Id no header")
        return 4
    reader = csv.reader(io.StringIO(head), delimiter=";")
    rows = list(reader)
    print(f"linhas_amostra={len(rows)} primeira_id={rows[1][0] if len(rows) > 1 else 'n/a'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
