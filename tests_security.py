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
import machine_limits
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

    def test_session_token_accepts_header_without_query(self):
        token = "01234567-89ab-cdef-0123-456789abcdef"
        with patch.object(api_server, "get_session", return_value=None) as gs:
            r = self.client.get(
                "/notificacao/bandeiras",
                headers={**self.headers, machine_limits.SESSION_HEADER: token},
            )
            self.assertEqual(r.status_code, 401)
            gs.assert_called_once_with(token)

    def test_session_token_query_rejected(self):
        token = "01234567-89ab-cdef-0123-456789abcdef"
        r = self.client.get(
            f"/notificacao/bandeiras?session_token={token}",
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 401)
        self.assertIn("query", r.json()["detail"]["mensagem"].lower())

    def test_session_token_body_rejected(self):
        token = "01234567-89ab-cdef-0123-456789abcdef"
        r = self.client.post(
            "/notificacao/filtrar",
            headers=self.headers,
            json={
                "session_token": token,
                "mensagem": "x",
                "destinatario": "C",
            },
        )
        self.assertEqual(r.status_code, 401)
        self.assertIn("body", r.json()["detail"]["mensagem"].lower())

    def test_x_forwarded_for_ignored_without_trusted_proxy(self):
        machine_limits.reset_trusted_proxies_cache_for_tests()
        with patch.dict(os.environ, {"MACHINE_TRUSTED_PROXY_CIDRS": ""}, clear=False):
            machine_limits.reset_trusted_proxies_cache_for_tests()
            ip_lim = machine_limits.SlidingWindowLimiter(1, 600.0)
            with patch.object(machine_limits, "login_ip_limiter", ip_lim):
                machine_limits.check_login_limits("a@b.invalid", "203.0.113.10")
                with self.assertRaises(Exception) as ctx:
                    machine_limits.check_login_limits("b@b.invalid", "203.0.113.10")
                self.assertEqual(getattr(ctx.exception, "status_code", None), 429)
            spoofed = machine_limits.client_ip_from_request(
                "203.0.113.10",
                {"X-Forwarded-For": "1.2.3.4, 5.6.7.8"},
            )
            self.assertEqual(spoofed, "203.0.113.10")

    def test_x_forwarded_for_used_from_trusted_proxy(self):
        with patch.dict(os.environ, {"MACHINE_TRUSTED_PROXY_IPS": "10.0.0.1"}, clear=False):
            machine_limits.reset_trusted_proxies_cache_for_tests()
            client = machine_limits.client_ip_from_request(
                "10.0.0.1",
                {"X-Forwarded-For": "198.51.100.44, 10.0.0.1"},
            )
            self.assertEqual(client, "198.51.100.44")

    def test_xff_strips_trusted_hops_right_to_left(self):
        with patch.dict(
            os.environ,
            {"MACHINE_TRUSTED_PROXY_IPS": "10.0.0.1,10.0.0.2"},
            clear=False,
        ):
            machine_limits.reset_trusted_proxies_cache_for_tests()
            client = machine_limits.client_ip_from_request(
                "10.0.0.2",
                {"X-Forwarded-For": "198.51.100.44, 10.0.0.1, 10.0.0.2"},
            )
            self.assertEqual(client, "198.51.100.44")

    def test_xff_proxy_appends_real_client_ignores_fake_left(self):
        """Cliente manda IP falso; proxy confiável acrescenta o IP real à direita."""
        with patch.dict(os.environ, {"MACHINE_TRUSTED_PROXY_IPS": "10.0.0.1"}, clear=False):
            machine_limits.reset_trusted_proxies_cache_for_tests()
            client = machine_limits.client_ip_from_request(
                "10.0.0.1",
                {"X-Forwarded-For": "1.2.3.4, 203.0.113.50"},
            )
            self.assertEqual(client, "203.0.113.50")
            ip_lim = machine_limits.SlidingWindowLimiter(1, 600.0)
            with patch.object(machine_limits, "login_ip_limiter", ip_lim):
                machine_limits.check_login_limits("a@b.invalid", client)
                with self.assertRaises(Exception) as ctx:
                    machine_limits.check_login_limits(
                        "c@b.invalid",
                        machine_limits.client_ip_from_request(
                            "10.0.0.1",
                            {"X-Forwarded-For": "9.9.9.9, 203.0.113.50"},
                        ),
                    )
                self.assertEqual(getattr(ctx.exception, "status_code", None), 429)

    def test_xff_malformed_chain_falls_back_to_peer(self):
        with patch.dict(os.environ, {"MACHINE_TRUSTED_PROXY_IPS": "10.0.0.1"}, clear=False):
            machine_limits.reset_trusted_proxies_cache_for_tests()
            peer = machine_limits.client_ip_from_request(
                "10.0.0.1",
                {"X-Forwarded-For": "not-an-ip, also-bad"},
            )
            self.assertEqual(peer, "10.0.0.1")
            only_trusted = machine_limits.client_ip_from_request(
                "10.0.0.1",
                {"X-Forwarded-For": "10.0.0.1"},
            )
            self.assertEqual(only_trusted, "10.0.0.1")

    def test_private_peer_not_auto_trusted_for_xff(self):
        machine_limits.reset_trusted_proxies_cache_for_tests()
        with patch.dict(os.environ, {"MACHINE_TRUSTED_PROXY_CIDRS": ""}, clear=False):
            machine_limits.reset_trusted_proxies_cache_for_tests()
            client = machine_limits.client_ip_from_request(
                "192.168.1.50",
                {"X-Forwarded-For": "8.8.8.8"},
            )
            self.assertEqual(client, "192.168.1.50")

    def test_parse_forwarded_for_chain_rejects_malformed(self):
        with self.assertRaises(machine_limits.MalformedForwardedFor):
            machine_limits.parse_forwarded_for_chain("1.2.3.4, , 5.6.7.8")
        with self.assertRaises(machine_limits.MalformedForwardedFor):
            machine_limits.parse_forwarded_for_chain("cliente-invalido")

    def test_login_rate_limit_returns_429(self):
        email = "rate-limit@example.invalid"
        email_lim = machine_limits.SlidingWindowLimiter(2, 600.0)
        ip_lim = machine_limits.SlidingWindowLimiter(100, 600.0)
        with patch.object(machine_limits, "login_email_limiter", email_lim), patch.object(
            machine_limits, "login_ip_limiter", ip_lim
        ):
            machine_limits.check_login_limits(email, "203.0.113.1")
            machine_limits.check_login_limits(email, "203.0.113.1")
            with self.assertRaises(Exception) as ctx:
                machine_limits.check_login_limits(email, "203.0.113.1")
            self.assertEqual(getattr(ctx.exception, "status_code", None), 429)

    def test_chrome_pool_queue_full_returns_429(self):
        with patch.dict(
            os.environ,
            {"MACHINE_CHROME_MAX": "1", "MACHINE_CHROME_QUEUE_MAX": "0", "MACHINE_CHROME_ACQUIRE_TIMEOUT_SEC": "0.01"},
        ):
            machine_limits.reset_all_limits_for_tests()
            pool = machine_limits._ConcurrencyPool("test", 1, 0, 0.01)
            with pool.slot():
                with self.assertRaises(Exception) as ctx:
                    with pool.slot():
                        pass
                self.assertEqual(getattr(ctx.exception, "status_code", None), 429)

if __name__=="__main__":unittest.main()
