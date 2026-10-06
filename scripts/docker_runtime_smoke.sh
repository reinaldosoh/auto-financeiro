#!/usr/bin/env bash
# Runtime checks inside the Docker image (non-root, TOTP volume, TLS, pools).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

API_KEY="${MACHINE_API_KEY:-synthetic-docker-runtime-$(openssl rand -hex 12)}"
FERNET_KEY="$(python3 - <<'PY'
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
PY
)"
IMAGE="${AUTO_FINANCEIRO_IMAGE:-auto-financeiro-runtime-smoke:local}"
TOTP_VOL="${AUTO_FINANCEIRO_TOTP_VOL:-auto-financeiro-runtime-smoke-totp}"
PLATFORM="linux/amd64"

# Volume de smoke isolado (não é o totp-store de produção no Easypanel).
docker volume rm "${TOTP_VOL}" >/dev/null 2>&1 || true

run() {
  docker run --rm --platform "$PLATFORM" \
    -v "${TOTP_VOL}:/data/totp" \
    "$@"
}

run_i() {
  docker run --rm -i --platform "$PLATFORM" \
    -v "${TOTP_VOL}:/data/totp" \
    "$@"
}

echo "== build ($PLATFORM, bookworm base for Chrome) =="
if ! docker build --platform "$PLATFORM" -t "$IMAGE" . ; then
  echo "FAIL: build da imagem amd64." >&2
  exit 1
fi

echo "== non-root + chrome + totp volume =="
run \
  -e MACHINE_API_KEY="$API_KEY" \
  -e TOTP_ENCRYPTION_KEY="$FERNET_KEY" \
  -e TOTP_STORE_PATH=/data/totp/chaves_totp.json \
  -e MACHINE_ENABLE_TOTP_ADMIN=0 \
  --entrypoint "" \
  "$IMAGE" \
  gosu machine:machine bash -ec '
set -e
id -u | grep -vx 0
whoami | grep -x machine
google-chrome-stable --version >/dev/null
python3 - <<PY
import os
from pathlib import Path
import totp_store
path = Path(os.environ["TOTP_STORE_PATH"])
totp_store.save(path, "runtime-smoke@example.invalid", "JBSWY3DPEHPK3PXP")
loaded = totp_store.load(path)
assert loaded["runtime-smoke@example.invalid"] == "JBSWY3DPEHPK3PXP"
assert path.stat().st_mode & 0o777 == 0o600
print("ok_totp_volume", path)
PY
'

echo "== HTTPS valid + invalid cert =="
run_i \
  -e MACHINE_API_KEY="$API_KEY" \
  -e TOTP_ENCRYPTION_KEY="$FERNET_KEY" \
  -e MACHINE_ENABLE_TOTP_ADMIN=0 \
  --entrypoint "" \
  "$IMAGE" \
  gosu machine:machine python3 - <<'PY'
import os, tempfile
import safe_image

good = tempfile.NamedTemporaryFile(delete=False).name
try:
    safe_image.download("https://www.google.com/favicon.ico", good)
    assert os.path.getsize(good) > 0
    print("ok_https_download", os.path.getsize(good))
finally:
    if os.path.exists(good):
        os.unlink(good)

bad = tempfile.NamedTemporaryFile(delete=False).name
try:
    try:
        safe_image.download("https://self-signed.badssl.com/", bad)
        raise SystemExit("expected TLS rejection")
    except safe_image.ImageRejected:
        print("ok_tls_rejected")
finally:
    if os.path.exists(bad):
        os.unlink(bad)
PY

echo "== concurrency pool cleanup =="
run_i \
  -e MACHINE_CHROME_MAX=1 \
  -e MACHINE_CHROME_QUEUE_MAX=0 \
  -e MACHINE_CHROME_ACQUIRE_TIMEOUT_SEC=0.05 \
  --entrypoint "" \
  "$IMAGE" \
  gosu machine:machine python3 - <<'PY'
import machine_limits
machine_limits.reset_all_limits_for_tests()
pool = machine_limits._ConcurrencyPool("smoke", 1, 0, 0.05)
with pool.slot():
    try:
        with pool.slot():
            raise SystemExit("expected 429")
    except Exception as e:
        assert getattr(e, "status_code", None) == 429
print("ok_chrome_pool_429")
PY

echo "== uvicorn worker non-root (smoke HTTP) =="
CID="$(docker run -d --platform "$PLATFORM" \
  -v "${TOTP_VOL}:/data/totp" \
  -e MACHINE_API_KEY="$API_KEY" \
  -e TOTP_ENCRYPTION_KEY="$FERNET_KEY" \
  -e TOTP_STORE_PATH=/data/totp/chaves_totp.json \
  -e MACHINE_ENABLE_TOTP_ADMIN=0 \
  --entrypoint "" \
  "$IMAGE" \
  gosu machine:machine bash -ec '/entrypoint.sh')"
trap 'docker rm -f "${CID}" >/dev/null 2>&1 || true' EXIT
for i in $(seq 1 30); do
  if docker exec "$CID" curl -sf -H "X-API-Key: $API_KEY" "http://127.0.0.1:8000/health" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
UVICORN_USER="$(docker exec "$CID" bash -ec 'ps -o user= -C uvicorn | head -1 | tr -d " "')"
test "$UVICORN_USER" = "machine"
docker exec "$CID" curl -sf -H "X-API-Key: $API_KEY" "http://127.0.0.1:8000/health" >/dev/null
echo "ok_uvicorn_health user=$UVICORN_USER"

echo "docker_runtime_smoke: OK (volume nomeado ${TOTP_VOL} — não sobrescreve produção)"
