"""Public HTTPS only; connect to validated IP with original hostname for TLS."""
import base64
import binascii
import ipaddress
import socket
import time
from pathlib import Path
from urllib.parse import urlsplit, urljoin
import certifi
import urllib3

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 3
TOTAL_SECONDS = 30

class ImageRejected(ValueError):
    pass

def resolve_public(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443):
            raise ImageRejected("Only public HTTPS URLs on port 443 are accepted")
        if any(ord(c) < 33 for c in url) or "\\" in url or "%" in parsed.hostname:
            raise ImageRejected("Invalid image URL")
        host = parsed.hostname.encode("idna").decode("ascii")
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses:
            raise ImageRejected("Image host could not be resolved")
        ips = [entry[4][0] for entry in addresses]
        for value in ips:
            ip = ipaddress.ip_address(value)
            mapped = getattr(ip, "ipv4_mapped", None)
            if (isinstance(ip, ipaddress.IPv6Address) and (ip.sixtofour or ip.teredo or ip in ipaddress.ip_network("64:ff9b::/96") or ip in ipaddress.ip_network("64:ff9b:1::/48"))) or not ip.is_global or ip.is_multicast or (mapped and (not mapped.is_global or mapped.is_multicast)):
                raise ImageRejected("Internal network destinations are blocked")
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        return host, ips[0], target
    except (ValueError, UnicodeError, OSError):
        raise ImageRejected("Image URL or destination is not allowed") from None

def download(url, destination):
    started = time.monotonic()
    try:
        for redirects in range(MAX_REDIRECTS + 1):
            if time.monotonic() - started > TOTAL_SECONDS:
                raise ImageRejected("Image download timeout")
            host, ip, target = resolve_public(url)
            # No environment proxy and no second hostname resolution (DNS rebinding).
            pool = urllib3.HTTPSConnectionPool(ip, port=443, server_hostname=host,
                assert_hostname=host, cert_reqs="CERT_REQUIRED", ca_certs=certifi.where(),
                timeout=urllib3.Timeout(connect=5, read=5), maxsize=1, retries=False)
            response = None
            try:
                response = pool.urlopen("GET", target, headers={"Host": host, "User-Agent": "MachineImage/1", "Accept-Encoding": "identity"},
                    assert_same_host=False, redirect=False, retries=False, preload_content=False)
                if response.status in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location or redirects == MAX_REDIRECTS:
                        raise ImageRejected("Too many or invalid image redirects")
                    url = urljoin(url, location)
                    continue
                if response.status != 200:
                    raise ImageRejected("Image download failed")
                length = response.headers.get("Content-Length")
                if length and (int(length) < 0 or int(length) > MAX_IMAGE_BYTES):
                    raise ImageRejected("Image exceeds size limit")
                size = 0
                with open(destination, "wb") as out:
                    for chunk in response.stream(65536, decode_content=False):
                        size += len(chunk)
                        if size > MAX_IMAGE_BYTES or time.monotonic() - started > TOTAL_SECONDS:
                            raise ImageRejected("Image exceeds download limits")
                        out.write(chunk)
                if not size:
                    raise ImageRejected("Empty image")
                return
            finally:
                if response is not None:
                    response.close()
                pool.close()
        raise ImageRejected("Too many image redirects")
    except Exception:
        Path(destination).unlink(missing_ok=True)
        raise ImageRejected("Image URL/download not allowed or limit exceeded") from None

def decode_image(value, destination):
    try:
        data = value.split(",", 1)[1] if value.startswith("data:") else value
        if len(data) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
            raise ImageRejected("Image exceeds size limit")
        raw = base64.b64decode(data, validate=True)
        if not raw or len(raw) > MAX_IMAGE_BYTES:
            raise ImageRejected("Invalid image size")
        Path(destination).write_bytes(raw)
    except (ValueError, binascii.Error):
        Path(destination).unlink(missing_ok=True)
        raise ImageRejected("Invalid base64 image or size limit exceeded") from None

def prepare_image(destination, image_url=None, image_base64=None):
    if image_base64:
        decode_image(image_base64, destination)
    elif image_url:
        download(image_url, destination)
    else:
        raise ImageRejected("Image required")
