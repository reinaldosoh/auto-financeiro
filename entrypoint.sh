#!/bin/bash
set -e

# Easypanel pode iniciar como root para ajustar volume persistente; worker roda como `machine`.
if [ "$(id -u)" = "0" ]; then
  mkdir -p /data/totp /home/machine/.cache /tmp/.X11-unix
  if [ -d /data/totp ]; then
    chown -R machine:machine /data/totp /home/machine 2>/dev/null || true
    chmod 700 /data/totp 2>/dev/null || true
  fi
  exec gosu machine:machine "$0" "$@"
fi

export HOME="${HOME:-/home/machine}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"
mkdir -p "$XDG_CACHE_HOME" /tmp/.X11-unix

# Inicia Xvfb com display virtual :99
Xvfb :99 -screen 0 1280x900x24 -ac &
export DISPLAY=:99

sleep 2

# Easypanel injeta PORT; proxy encaminha para 0.0.0.0 (não loopback dentro do container).
PORT="${PORT:-8000}"
exec uvicorn api_server:app --host 0.0.0.0 --port "${PORT}"
