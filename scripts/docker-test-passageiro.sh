#!/usr/bin/env bash
# Roda o cadastro de anúncio passageiro dentro da MESMA imagem Docker da API (Chrome + Xvfb).
# Uso: na pasta do projeto: ./scripts/docker-test-passageiro.sh
# Credenciais: export TM_EMAIL TM_SENHA TM_TOTP antes, ou edite run_passageiro_local.py
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo ">>> Build (se necessário)…"
docker compose build -q

echo ">>> Teste passageiro no container (Xvfb + DOCKER=true)…"
docker compose run --rm \
  --shm-size=2g \
  --entrypoint "" \
  -e DOCKER=true \
  -e DISPLAY=:99 \
  auto-financeiro \
  bash -lc '
    set -e
    Xvfb :99 -screen 0 1280x900x24 -ac &
    sleep 2
    export DISPLAY=:99
    cd /app
    python3 run_passageiro_local.py --headless --baixar-imagem
  '

echo ">>> OK"
