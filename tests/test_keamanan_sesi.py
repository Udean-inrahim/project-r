import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from app import ambil_secret_key, app

TOKEN_UJI = "token-uji-csrf"


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

    def test_post_tanpa_token_ditolak(self):
        with self.client.session_transaction() as session:
            session["id_guru"] = 1
            session["_csrf_token"] = TOKEN_UJI

        self.assertEqual(self.client.post("/logout").status_code, 400)

    def test_post_dengan_token_salah_ditolak(self):
        with self.client.session_transaction() as session:
            session["id_guru"] = 1
            session["_csrf_token"] = TOKEN_UJI

        response = self.client.post(
            "/logout", data={"csrf_token": "token-palsu"}
        )

        self.assertEqual(response.status_code, 400)

    def test_logout_post_menghapus_sesi(self):
        with self.client.session_transaction() as session:
            session["id_guru"] = 1
            session["_csrf_token"] = TOKEN_UJI

        response = self.client.post(
            "/logout", data={"csrf_token": TOKEN_UJI}
        )

        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertNotIn("id_guru", session)


class CsrfTemplateTest(unittest.TestCase):
    def test_semua_form_post_punya_token(self):
        folder = Path(__file__).resolve().parent.parent / "templates"
        pola = re.compile(r"<form\b[^>]*>", re.IGNORECASE)
        tanpa_token = []

        for path in folder.rglob("*.html"):
            teks = path.read_text(encoding="utf-8")
            for form in pola.finditer(teks):
                if 'method="post"' not in form.group(0).lower():
                    continue

                sisa = teks[form.end():]
                akhir = sisa.lower().find("</form>")
                isi = sisa[:akhir] if akhir != -1 else sisa
                if "csrf_token" not in isi:
                    baris = teks.count("\n", 0, form.start()) + 1
                    tanpa_token.append(f"{path.name}:{baris}")

        self.assertEqual(
            tanpa_token, [], f"Form POST tanpa token CSRF: {tanpa_token}"
        )


if __name__ == "__main__":
    unittest.main()
