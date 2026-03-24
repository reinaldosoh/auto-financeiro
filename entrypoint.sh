#!/bin/bash
set -e

# Inicia Xvfb com display virtual :99
Xvfb :99 -screen 0 1280x900x24 -ac &
export DISPLAY=:99

# Aguarda Xvfb iniciar
sleep 2

# Inicia o servidor FastAPI
exec uvicorn api_server:app --host 0.0.0.0 --port 8000
