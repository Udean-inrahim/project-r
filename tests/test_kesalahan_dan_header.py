import unittest
from unittest.mock import patch

from app import app


class KoneksiSehat:
    def execute(self, *args, **kwargs):
        return self

    def fetchone(self):
        return (1,)

    def close(self):
        pass


class HeaderKeamananTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def healthz(self):
        with patch("app.get_db_connection", return_value=KoneksiSehat()):
            return self.client.get("/healthz")

    def test_header_keamanan_terpasang(self):
        respons = self.healthz()

        self.assertEqual(respons.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(respons.headers["X-Frame-Options"], "DENY")
        self.assertIn(
            "frame-ancestors 'none'",
            respons.headers["Content-Security-Policy"],
        )

    def test_healthz_tidak_membocorkan_data(self):
        data = self.healthz().get_json()

        self.assertEqual(data["database"], "ok")
        self.assertNotIn("jumlah_guru", data)
        self.assertFalse(
            [kunci for kunci in data if kunci.startswith("galat")]
        )

    def test_halaman_404_kustom(self):
        respons = self.client.get("/halaman-yang-tidak-ada")

        self.assertEqual(respons.status_code, 404)
        self.assertIn(b"404", respons.data)
        self.assertEqual(respons.headers["X-Frame-Options"], "DENY")

    def test_csrf_gagal_memakai_halaman_400(self):
        respons = self.client.post("/logout")

        self.assertEqual(respons.status_code, 400)
        self.assertIn(b"Token CSRF", respons.data)


if __name__ == "__main__":
    unittest.main()
