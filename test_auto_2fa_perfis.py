"""Perfis do Chrome e drivers abertos (sem navegador real)."""
import os
import tempfile
import time
import unittest
from unittest import mock

import auto_2fa


class FakeDriver:
    def __init__(self):
        self.encerrado = 0

    def implicitly_wait(self, _):
        pass

    def quit(self):
        self.encerrado += 1


class DriversAbertosTests(unittest.TestCase):
    def test_novo_driver_do_mesmo_email_encerra_o_anterior(self):
        d = auto_2fa._DriversAbertos()
        a, b = FakeDriver(), FakeDriver()
        d["x@y.com"] = a
        d["x@y.com"] = b
        self.assertEqual((a.encerrado, b.encerrado), (1, 0))

    def test_mesmo_driver_nao_e_encerrado(self):
        d = auto_2fa._DriversAbertos()
        a = FakeDriver()
        d["x@y.com"] = a
        d["x@y.com"] = a
        self.assertEqual(a.encerrado, 0)

    def test_emails_diferentes_nao_se_afetam(self):
        d = auto_2fa._DriversAbertos()
        a, b = FakeDriver(), FakeDriver()
        d["a@y.com"] = a
        d["b@y.com"] = b
        self.assertEqual((a.encerrado, b.encerrado), (0, 0))


class PerfisChromeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        auto_2fa._ultima_limpeza_perfis = 0.0
        self.addCleanup(self.tmp.cleanup)

    def _perfil(self, nome, idade_h):
        caminho = os.path.join(self.root, nome)
        os.makedirs(os.path.join(caminho, "Default"))
        antigo = time.time() - idade_h * 3600
        os.utime(caminho, (antigo, antigo))
        return caminho

    def test_remove_so_perfis_run_antigos_e_sem_driver(self):
        velho = self._perfil("run-aaa", 10)
        recente = self._perfil("run-bbb", 1)
        em_uso = self._perfil("run-ccc", 10)
        outro = self._perfil("outra-pasta", 10)
        drv = FakeDriver()
        drv._perfil_dir = em_uso
        auto_2fa._drivers_abertos["teste@x.com"] = drv
        try:
            n = auto_2fa._limpar_perfis_antigos(self.root)
        finally:
            auto_2fa._drivers_abertos.pop("teste@x.com", None)
        self.assertEqual(n, 1)
        self.assertFalse(os.path.exists(velho))
        for p in (recente, em_uso, outro):
            self.assertTrue(os.path.exists(p), p)

    def test_varredura_tem_intervalo_minimo(self):
        self._perfil("run-aaa", 10)
        self.assertEqual(auto_2fa._limpar_perfis_antigos(self.root), 1)
        self._perfil("run-bbb", 10)
        self.assertEqual(auto_2fa._limpar_perfis_antigos(self.root), 0)

    def test_quit_apaga_o_perfil_do_driver(self):
        fake = FakeDriver()
        with mock.patch.dict(os.environ, {"CHROME_USER_DATA_DIR": self.root, "DOCKER": ""}), \
             mock.patch.object(auto_2fa.webdriver, "Chrome", return_value=fake), \
             mock.patch.object(auto_2fa, "_macos_strip_quarantine_driver_caches"):
            driver = auto_2fa.criar_driver(headless=True)
        perfis = [n for n in os.listdir(self.root) if n.startswith("run-")]
        self.assertEqual(len(perfis), 1)
        driver.quit()
        self.assertEqual(fake.encerrado, 1)
        self.assertEqual([n for n in os.listdir(self.root) if n.startswith("run-")], [])

    def test_falha_ao_abrir_chrome_nao_deixa_perfil(self):
        erro = auto_2fa.WebDriverException("falhou")
        with mock.patch.dict(os.environ, {"CHROME_USER_DATA_DIR": self.root, "DOCKER": ""}), \
             mock.patch.object(auto_2fa.webdriver, "Chrome", side_effect=erro), \
             mock.patch.object(auto_2fa, "ChromeDriverManager") as cdm, \
             mock.patch.object(auto_2fa, "_macos_strip_quarantine_driver_caches"):
            cdm.return_value.install.return_value = "/nao/existe"
            with self.assertRaises(auto_2fa.WebDriverException):
                auto_2fa.criar_driver(headless=True)
        self.assertEqual([n for n in os.listdir(self.root) if n.startswith("run-")], [])


if __name__ == "__main__":
    unittest.main()
