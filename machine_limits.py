"""Rate limits, Chrome concurrency pool, and heavy HTTP job slots."""
from __future__ import annotations

import ipaddress
import os
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from functools import lru_cache
from typing import Callable, Deque, Dict, List, Optional, Sequence, TypeVar

from fastapi import HTTPException

T = TypeVar("T")

SESSION_HEADER = "X-Session-Token"


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, value)


def _float_env(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, value)


class SlidingWindowLimiter:
    """Thread-safe sliding window counter per key."""

    def __init__(self, max_events: int, window_sec: float):
        self._max = max_events
        self._window = window_sec
        self._events: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            bucket = self._events[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self._max:
                return False
            bucket.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


login_email_limiter = SlidingWindowLimiter(
    _int_env("MACHINE_LOGIN_RATE_MAX", 12),
    _float_env("MACHINE_LOGIN_RATE_WINDOW_SEC", 300.0),
)
login_ip_limiter = SlidingWindowLimiter(
    _int_env("MACHINE_LOGIN_IP_RATE_MAX", 40),
    _float_env("MACHINE_LOGIN_IP_RATE_WINDOW_SEC", 300.0),
)
heavy_limiter = SlidingWindowLimiter(
    _int_env("MACHINE_HEAVY_RATE_MAX", 20),
    _float_env("MACHINE_HEAVY_RATE_WINDOW_SEC", 60.0),
)


def _normalize_ip_candidate(raw: str) -> Optional[str]:
    candidate = (raw or "").strip().strip('"').strip("'")
    if not candidate:
        return None
    if candidate.startswith("[") and "]" in candidate:
        host = candidate[1 : candidate.index("]")]
        try:
            return str(ipaddress.ip_address(host))
        except ValueError:
            return None
    if candidate.count(":") == 1 and "." in candidate:
        host, port = candidate.rsplit(":", 1)
        if port.isdigit():
            candidate = host
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def parse_forwarded_for_chain(value: str) -> List[str]:
    """Parse X-Forwarded-For left-to-right; drop invalid hops."""
    if not value:
        return []
    chain: List[str] = []
    for part in value.split(","):
        normalized = _normalize_ip_candidate(part)
        if normalized:
            chain.append(normalized)
    return chain


@lru_cache(maxsize=1)
def _trusted_proxy_networks() -> tuple:
    raw = (
        os.environ.get("MACHINE_TRUSTED_PROXY_CIDRS", "").strip()
        or os.environ.get("MACHINE_TRUSTED_PROXY_IPS", "").strip()
    )
    networks = []
    hosts: List[str] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            if "/" in item:
                networks.append(ipaddress.ip_network(item, strict=False))
            else:
                hosts.append(str(ipaddress.ip_address(item)))
        except ValueError:
            continue
    return tuple(networks), tuple(hosts)


def _peer_is_trusted_proxy(peer_host: str) -> bool:
    normalized = _normalize_ip_candidate(peer_host)
    if not normalized:
        return False
    try:
        addr = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    networks, hosts = _trusted_proxy_networks()
    if normalized in hosts:
        return True
    return any(addr in net for net in networks)


def client_ip_from_request(peer_host: Optional[str], headers: dict) -> str:
    """
    Rate-limit key: TCP peer by default.
    X-Forwarded-For / X-Real-Ip only when the immediate peer is a trusted proxy.
    """
    peer = _normalize_ip_candidate(peer_host or "") or "unknown"
    if peer == "unknown" or not _peer_is_trusted_proxy(peer):
        return peer

    forwarded = headers.get("x-forwarded-for") or headers.get("X-Forwarded-For") or ""
    chain = parse_forwarded_for_chain(forwarded)
    if chain:
        return chain[0]

    real_ip = headers.get("x-real-ip") or headers.get("X-Real-Ip") or ""
    chain_real = parse_forwarded_for_chain(real_ip)
    if chain_real:
        return chain_real[0]

    return peer


def client_ip_from_headers(headers: dict, *, peer_host: Optional[str] = None) -> str:
    """Backward-compatible wrapper; prefer client_ip_from_request with Request.client."""
    return client_ip_from_request(peer_host, headers)


def reset_trusted_proxies_cache_for_tests() -> None:
    _trusted_proxy_networks.cache_clear()


def _rate_limit_429(detail: str) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail={"sucesso": False, "mensagem": detail},
        headers={"Retry-After": "60"},
    )


def check_login_limits(email: str, client_ip: str) -> None:
    key_email = (email or "").strip().lower()
    if not login_email_limiter.allow(key_email or "unknown"):
        raise _rate_limit_429("Muitas tentativas de login para esta conta. Tente novamente em alguns minutos.")
    if not login_ip_limiter.allow(client_ip or "unknown"):
        raise _rate_limit_429("Muitas tentativas de login a partir deste endereço. Tente novamente em alguns minutos.")


def check_heavy_limit(bucket: str) -> None:
    if not heavy_limiter.allow(bucket):
        raise _rate_limit_429("Limite de operações pesadas atingido. Tente novamente em instantes.")


class _ConcurrencyPool:
    """Global cap with bounded wait queue and guaranteed release."""

    def __init__(self, name: str, max_active: int, max_waiting: int, acquire_timeout_sec: float):
        self.name = name
        self._max_active = max(1, max_active)
        self._max_waiting = max(0, max_waiting)
        self._acquire_timeout = acquire_timeout_sec
        self._active = 0
        self._waiting = 0
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

    @contextmanager
    def slot(self):
        if not self._enter():
            raise _rate_limit_429(
                f"Servidor ocupado ({self.name}). Fila cheia ou tempo esgotado — tente novamente."
            )
        try:
            yield
        finally:
            self._leave()

    def _enter(self) -> bool:
        deadline = time.monotonic() + self._acquire_timeout
        with self._cond:
            while self._active >= self._max_active:
                if self._waiting >= self._max_waiting:
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._waiting += 1
                self._cond.wait(timeout=remaining)
                self._waiting = max(0, self._waiting - 1)
                if time.monotonic() >= deadline:
                    return False
            self._active += 1
            return True

    def _leave(self) -> None:
        with self._cond:
            self._active = max(0, self._active - 1)
            self._cond.notify()

    def reset_for_tests(self) -> None:
        with self._cond:
            self._active = 0
            self._waiting = 0
            self._cond.notify_all()


chrome_pool = _ConcurrencyPool(
    "chrome",
    _int_env("MACHINE_CHROME_MAX", 2),
    _int_env("MACHINE_CHROME_QUEUE_MAX", 6),
    _float_env("MACHINE_CHROME_ACQUIRE_TIMEOUT_SEC", 120.0),
)

heavy_http_pool = _ConcurrencyPool(
    "heavy-http",
    _int_env("MACHINE_HEAVY_CONCURRENT_MAX", 4),
    _int_env("MACHINE_HEAVY_QUEUE_MAX", 8),
    _float_env("MACHINE_HEAVY_ACQUIRE_TIMEOUT_SEC", 90.0),
)


def run_with_chrome(fn: Callable[..., T], *args, **kwargs) -> T:
    with chrome_pool.slot():
        return fn(*args, **kwargs)


def run_with_heavy_http(fn: Callable[..., T], *args, **kwargs) -> T:
    with heavy_http_pool.slot():
        return fn(*args, **kwargs)


def reset_all_limits_for_tests() -> None:
    login_email_limiter.reset()
    login_ip_limiter.reset()
    heavy_limiter.reset()
    chrome_pool.reset_for_tests()
    heavy_http_pool.reset_for_tests()
    reset_trusted_proxies_cache_for_tests()
