import os
import unittest
from unittest.mock import patch

from app import ambil_secret_key, app


class KeamananSesiTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_secret_key_wajib_diatur(self):
        with patch.dict(os.environ, {"SECRET_KEY": ""}):
            with self.assertRaises(RuntimeError):
                ambil_secret_key()

    def test_secret_key_dipakai_saat_tersedia(self):
        with patch.dict(os.environ, {"SECRET_KEY": "kunci-uji"}):
            self.assertEqual(ambil_secret_key(), "kunci-uji")

    def test_cookie_sesi_httponly_dan_samesite(self):
        self.assertTrue(app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(app.config["SESSION_COOKIE_SAMESITE"], "Lax")

    @unittest.skipIf(
        os.environ.get("VERCEL") == "1",
        "Lingkungan Vercel memang memakai cookie Secure.",
    )
    def test_cookie_secure_hanya_di_produksi(self):
        self.assertFalse(app.config["SESSION_COOKIE_SECURE"])

    def test_logout_menolak_get(self):
        self.assertEqual(self.client.get("/logout").status_code, 405)

    def test_logout_post_menghapus_sesi(self):
        with self.client.session_transaction() as session:
            session["id_guru"] = 1

        response = self.client.post("/logout")

        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertNotIn("id_guru", session)


if __name__ == "__main__":
    unittest.main()
