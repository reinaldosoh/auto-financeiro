#!/usr/bin/env bash
# Smoke HTTPS real dentro do runtime Docker (OpenSSL da imagem, não LibreSSL do macOS).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

API_KEY="${MACHINE_API_KEY:-synthetic-docker-smoke-key-$(openssl rand -hex 16)}"
FERNET_KEY="$(python3 - <<'PY'
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
PY
)"

docker compose build --quiet
docker compose run --rm --no-deps \
  -e MACHINE_API_KEY="$API_KEY" \
  -e TOTP_ENCRYPTION_KEY="$FERNET_KEY" \
  -e MACHINE_ENABLE_TOTP_ADMIN=0 \
  auto-financeiro \
  python3 - <<'PY'
import os, tempfile
import safe_image

path = tempfile.NamedTemporaryFile(delete=False).name
try:
    safe_image.download("https://www.google.com/favicon.ico", path)
    data = open(path, "rb").read()
    assert len(data) > 0, "download vazio"
    print("ok_https_download_bytes", len(data))
finally:
    if os.path.exists(path):
        os.unlink(path)
PY

echo "docker_https_smoke: OK"
