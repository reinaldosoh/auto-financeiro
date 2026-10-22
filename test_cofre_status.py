"""POST /cofre/status: só booleano, nunca o segredo."""
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import api_server

API_KEY = "synthetic-only-api-key-" + "x" * 32


class CofreStatusTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"MACHINE_API_KEY": API_KEY})
        self.env.start()
        self.client = TestClient(api_server.app)
        self.h = {"X-API-Key": API_KEY}
        self.cofre = patch.object(
            api_server, "carregar_chaves",
            return_value={"mariana@x.com": "SEGREDOSECRETO123", "vazio@x.com": ""},
        )
        self.cofre.start()

    def tearDown(self):
        self.cofre.stop()
        self.client.close()
        self.env.stop()

    def test_exige_api_key(self):
        r = self.client.post("/cofre/status", json={"emails": ["a@x.com"]})
        self.assertEqual(r.status_code, 401)

    def test_booleano_por_email_sem_vazar_segredo(self):
        r = self.client.post(
            "/cofre/status",
            headers=self.h,
            json={"emails": ["Mariana@X.com ", "outra@x.com", "vazio@x.com", "mariana@x.com", ""]},
        )
        self.assertEqual(r.status_code, 200)
        corpo = r.json()
        self.assertEqual(
            corpo["resultados"],
            [
                {"email": "mariana@x.com", "no_cofre": True},
                {"email": "outra@x.com", "no_cofre": False},
                {"email": "vazio@x.com", "no_cofre": False},
            ],
        )
        self.assertEqual((corpo["total"], corpo["no_cofre"]), (3, 1))
        self.assertNotIn("SEGREDOSECRETO123", r.text)

    def test_limite_de_500(self):
        r = self.client.post("/cofre/status", headers=self.h, json={"emails": [f"{i}@x.com" for i in range(501)]})
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
