"""Encrypted, atomic TOTP store. The encryption key never lives in this file."""
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken

PREFIX = b"TOTP-FERNET-v1\n"

def _cipher():
    key = os.environ.get("TOTP_ENCRYPTION_KEY", "")
    key_file = os.environ.get("TOTP_ENCRYPTION_KEY_FILE", "")
    if key and key_file:
        raise RuntimeError("Configure only one TOTP encryption key source")
    if key_file:
        key = Path(key_file).read_text().strip()
    if not key:
        raise RuntimeError("TOTP encryption key not configured")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        raise RuntimeError("Invalid TOTP encryption key") from None

@contextmanager
def _lock(path):
    lock = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(lock, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield
    finally:
        os.close(lock)

def _validate(data):
    if not isinstance(data, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in data.items()):
        raise RuntimeError("Invalid TOTP store")
    return data

def _read(path, cipher):
    if not path.exists():
        return {}
    raw = path.read_bytes()
    if not raw.startswith(PREFIX):
        raise RuntimeError("Legacy plaintext TOTP store: migrate explicitly before use")
    try:
        return _validate(json.loads(cipher.decrypt(raw[len(PREFIX):])))
    except (InvalidToken, ValueError, UnicodeError):
        raise RuntimeError("Unable to decrypt TOTP store") from None

def _write(path, data, cipher):
    fd, name = tempfile.mkstemp(prefix=".totp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(PREFIX + cipher.encrypt(json.dumps(data).encode()))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(name):
            os.unlink(name)

def load(path):
    path = Path(path)
    cipher = _cipher()
    with _lock(path):
        return _read(path, cipher)

def save(path, email, secret):
    path = Path(path)
    cipher = _cipher()
    with _lock(path):
        data = _read(path, cipher)
        data[email.lower()] = secret
        _write(path, data, cipher)

def migrate(path):
    """Explicit maintenance command; writes only after decrypt/readback succeeds."""
    path = Path(path)
    cipher = _cipher()
    with _lock(path):
        raw = path.read_bytes()
        if raw.startswith(PREFIX):
            _read(path, cipher)
            return
        data = _validate(json.loads(raw))
        token = cipher.encrypt(json.dumps(data).encode())
        assert json.loads(cipher.decrypt(token)) == data
        _write(path, data, cipher)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["migrate"])
    parser.add_argument("path")
    args = parser.parse_args()
    migrate(args.path)
    print("TOTP store encrypted and verified; no secrets printed")
