#!/usr/bin/env bash
# Testa remoção de anúncio passageiro na mesma imagem Docker da API (Chrome + Xvfb).
#
#   export TM_EMAIL TM_SENHA TM_TOTP
#   export REM_INDICE=1    # dom_slot_idx típico; padrão 1 se não definir
#   ./scripts/docker-test-remover-passageiro.sh
#
# Remover todos no container (cuidado): defina REM_TODOS=1
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

REM_INDICE="${REM_INDICE:-1}"

echo ">>> Build (se necessário)…"
docker compose build -q

if [[ "${REM_TODOS:-0}" == "1" ]]; then
  echo ">>> Remover TODOS os anúncios passageiro no container…"
  CMD='python3 run_remover_passageiro_local.py --headless'
else
  echo ">>> Remover passageiro no container (índice/slot ${REM_INDICE})…"
  CMD="python3 run_remover_passageiro_local.py --headless --indice ${REM_INDICE}"
fi

docker compose run --rm \
  --shm-size=2g \
  --entrypoint "" \
  -e DOCKER=true \
  -e DISPLAY=:99 \
  auto-financeiro \
  bash -lc "
    set -e
    Xvfb :99 -screen 0 1280x900x24 -ac &
    sleep 2
    export DISPLAY=:99
    cd /app
    ${CMD}
  "

echo ">>> OK"
