import base64
import io
import json
import logging
import os
import socket
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from cryptography.fernet import Fernet
import api_server
import api_security
import safe_image
import totp_store
import auto_2fa

API_KEY = "synthetic-only-api-key-" + "x" * 32
PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))]

class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "totp.json"
        self.env = patch.dict(os.environ, {"MACHINE_API_KEY": API_KEY,
            "TOTP_ENCRYPTION_KEY": Fernet.generate_key().decode(), "TOTP_ENCRYPTION_KEY_FILE": "",
            "MACHINE_ENABLE_TOTP_ADMIN": "0"})
        self.env.start()
        self.client = TestClient(api_server.app)
        self.headers = {"X-API-Key": API_KEY}

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.directory.cleanup()

    def test_all_routes_require_key_before_action(self):
        for route in api_server.app.routes:
            path = route.path
            for parameter in ["report_id", "job_id", "id_machine", "os_id", "notificacao_id"]:
                path = path.replace("{" + parameter + "}", "1")
            for method in route.methods:
                with self.subTest(path=path, method=method):
                    self.assertEqual(self.client.request(method, path).status_code, 401)
                    self.assertEqual(self.client.request(method, path, headers={"X-API-Key": "wrong"}).status_code, 401)

    def test_missing_configuration_fails_closed(self):
        with patch.dict(os.environ, {"MACHINE_API_KEY": ""}):
            self.assertEqual(self.client.get("/health").status_code, 503)

    def test_authenticated_health_and_disabled_docs_totp_admin(self):
        self.assertEqual(self.client.get("/health", headers=self.headers).status_code, 200)
        for path in ["/docs", "/redoc", "/openapi.json", "/chaves"]:
            self.assertEqual(self.client.get(path, headers=self.headers).status_code, 404)
        self.assertEqual(self.client.post("/codigo", headers=self.headers, json={"email": "qa@example.invalid"}).status_code, 404)

    def test_large_body_denied(self):
        self.assertEqual(self.client.post("/login", headers={**self.headers, "Content-Length": str(api_security.MAX_BODY_BYTES+1)}, content=b"{}").status_code, 413)
        data = b"x" * (api_security.MAX_BODY_BYTES+1)
        self.assertEqual(self.client.post("/login", headers=self.headers, content=iter([data[:4000000], data[4000000:]])).status_code, 413)

    def test_totp_encrypted_roundtrip_permissions(self):
        secret = "JBSWY3DPEHPK3PXP"
        totp_store.save(self.path, "QA@EXAMPLE.INVALID", secret)
        self.assertEqual(totp_store.load(self.path), {"qa@example.invalid": secret})
        self.assertNotIn(secret.encode(), self.path.read_bytes())
        self.assertNotIn(b"qa@example.invalid", self.path.read_bytes())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_totp_requires_key(self):
        with patch.dict(os.environ, {"TOTP_ENCRYPTION_KEY": ""}):
            with self.assertRaises(RuntimeError):totp_store.load(self.path)

    def test_wrong_key_and_tampering_fail_closed(self):
        totp_store.save(self.path, "qa", "synthetic-secret")
        with patch.dict(os.environ, {"TOTP_ENCRYPTION_KEY": Fernet.generate_key().decode()}):
            with self.assertRaises(RuntimeError):totp_store.load(self.path)
        self.path.write_bytes(self.path.read_bytes()[:-4] + b"bad!")
        with self.assertRaises(RuntimeError):totp_store.load(self.path)

    def test_plaintext_requires_explicit_verified_migration(self):
        data = {"qa@example.invalid": "JBSWY3DPEHPK3PXP"}
        self.path.write_text(json.dumps(data))
        with self.assertRaises(RuntimeError):totp_store.load(self.path)
        with self.assertRaises(RuntimeError):totp_store.save(self.path, "other", "secret")
        totp_store.migrate(self.path)
        self.assertEqual(totp_store.load(self.path), data)
        self.assertNotIn(b"JBSWY3DPEHPK3PXP", self.path.read_bytes())

    def test_concurrent_store_no_lost_updates(self):
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda i:totp_store.save(self.path, str(i), "synthetic"), range(12)))
        self.assertEqual(len(totp_store.load(self.path)), 12)

    def test_secret_not_logged_on_save(self):
        capture = io.StringIO(); handler = logging.StreamHandler(capture)
        auto_2fa.log.addHandler(handler)
        try:
            with patch.object(auto_2fa, "CHAVES_FILE", str(self.path)):
                auto_2fa.salvar_chave("qa", "JBSWY3DPEHPK3PXP")
            self.assertNotIn("JBSWY3DPEHPK3PXP", capture.getvalue())
        finally:auto_2fa.log.removeHandler(handler)

    def test_scheme_credentials_port_rejected_without_network(self):
        for url in ["http://example.com/a", "file:///etc/passwd", "https://user:pass@example.com/a", "https://example.com:8000/a", "https://example.com\\@localhost/a"]:
            with self.subTest(url=url), patch.object(safe_image.socket, "getaddrinfo") as dns:
                with self.assertRaises(safe_image.ImageRejected):safe_image.resolve_public(url)
                dns.assert_not_called()

    def test_private_loopback_linklocal_multicast_and_mixed_dns_denied(self):
        for ip in ["127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0", "224.0.0.1", "::1", "fc00::1", "fe80::1", "::ffff:127.0.0.1", "64:ff9b::7f00:1", "2002:7f00:1::1", "2001::1"]:
            private = (socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))
            with self.subTest(ip=ip), patch.object(safe_image.socket, "getaddrinfo", return_value=PUBLIC+[private]):
                with self.assertRaises(safe_image.ImageRejected):safe_image.resolve_public("https://image.example/a")

    def response(self, status=200, headers=None, chunks=None):
        response = MagicMock(status=status, headers=headers or {})
        response.stream.return_value = chunks or [b"synthetic-image"]
        return response

    def test_download_pins_ip_and_checks_tls_hostname(self):
        pool = MagicMock();pool.urlopen.return_value = self.response()
        with patch.object(safe_image.socket, "getaddrinfo", return_value=PUBLIC) as dns, patch.object(safe_image.urllib3, "HTTPSConnectionPool", return_value=pool) as factory:
            safe_image.download("https://image.example/a", self.path)
            dns.assert_called_once()
            self.assertEqual(factory.call_args.args[0], "1.1.1.1")
            self.assertEqual(factory.call_args.kwargs["server_hostname"], "image.example")
            self.assertEqual(factory.call_args.kwargs["assert_hostname"], "image.example")
            self.assertEqual(factory.call_args.kwargs["cert_reqs"], "CERT_REQUIRED")
            self.assertFalse(pool.urlopen.call_args.kwargs["redirect"])
            self.assertEqual(self.path.read_bytes(), b"synthetic-image")

    def test_redirect_to_internal_denied(self):
        pool=MagicMock();pool.urlopen.return_value=self.response(302,{"Location":"https://internal.example/a"})
        private=[(socket.AF_INET,socket.SOCK_STREAM,6,"",("127.0.0.1",443))]
        with patch.object(safe_image.socket,"getaddrinfo",side_effect=[PUBLIC,private]),patch.object(safe_image.urllib3,"HTTPSConnectionPool",return_value=pool) as factory:
            with self.assertRaises(safe_image.ImageRejected):safe_image.download("https://image.example/a",self.path)
            factory.assert_called_once()
            self.assertFalse(self.path.exists())

    def test_tls_failure_no_insecure_retry(self):
        pool=MagicMock();pool.urlopen.side_effect=safe_image.urllib3.exceptions.SSLError("bad certificate")
        with patch.object(safe_image.socket,"getaddrinfo",return_value=PUBLIC),patch.object(safe_image.urllib3,"HTTPSConnectionPool",return_value=pool):
            with self.assertRaises(safe_image.ImageRejected):safe_image.download("https://image.example/a",self.path)
            pool.urlopen.assert_called_once()

    def test_redirect_limit_size_header_and_stream(self):
        for response in [self.response(302,{"Location":"/again"}),self.response(headers={"Content-Length":str(safe_image.MAX_IMAGE_BYTES+1)}),self.response(chunks=[b"x"*(safe_image.MAX_IMAGE_BYTES+1)])]:
            pool=MagicMock();pool.urlopen.return_value=response
            with patch.object(safe_image.socket,"getaddrinfo",return_value=PUBLIC),patch.object(safe_image.urllib3,"HTTPSConnectionPool",return_value=pool):
                with self.assertRaises(safe_image.ImageRejected):safe_image.download("https://image.example/a",self.path)
                self.assertFalse(self.path.exists())

    def test_base64_validation_and_limit(self):
        safe_image.decode_image(base64.b64encode(b"synthetic-image").decode(),self.path)
        self.assertEqual(self.path.read_bytes(),b"synthetic-image")
        for value in ["@@@",base64.b64encode(b"x"*(safe_image.MAX_IMAGE_BYTES+1)).decode()]:
            with self.assertRaises(safe_image.ImageRejected):safe_image.decode_image(value,self.path)

    def test_three_routes_use_safe_helper_before_automation(self):
        for path in ["/anuncio-motorista","/anuncio-passageiro","/banner-corrida"]:
            with self.subTest(path=path),patch.object(api_server,"prepare_image",side_effect=safe_image.ImageRejected("blocked")) as image:
                result=self.client.post(path,headers=self.headers,json={"email":"qa@example.invalid","senha":"synthetic","imagem_url":"https://internal.example/a","link_anuncio":"https://example.com"})
                self.assertEqual(result.status_code,400)
                image.assert_called_once()

if __name__=="__main__":unittest.main()
