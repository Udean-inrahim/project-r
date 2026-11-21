import unittest
from unittest.mock import patch

import app as modul_app
from app import app, boleh_coba_login, catat_gagal_login

TOKEN = "token-uji-csrf"


class KoneksiKosong:
    def execute(self, *args, **kwargs):
        return self

    def fetchone(self):
        return None

    def commit(self):
        pass

    def close(self):
        pass


class BatasPercobaanLoginTest(unittest.TestCase):
    def setUp(self):
        modul_app._percobaan_login.clear()

    def test_percobaan_kelima_masih_diizinkan(self):
        kunci = ("127.0.0.1", "budi")
        for detik in range(5):
            self.assertTrue(boleh_coba_login(kunci, sekarang=detik))
            catat_gagal_login(kunci, sekarang=detik)
        self.assertFalse(boleh_coba_login(kunci, sekarang=5))

    def test_setelah_jeda_diizinkan_lagi(self):
        kunci = ("127.0.0.1", "budi")
        for _ in range(5):
            catat_gagal_login(kunci, sekarang=0)

        self.assertFalse(boleh_coba_login(kunci, sekarang=1))
        self.assertTrue(boleh_coba_login(kunci, sekarang=901))

    def test_login_keenam_mengembalikan_429(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session["_csrf_token"] = TOKEN

        data = {
            "username": "budi",
            "password": "salah",
            "csrf_token": TOKEN,
        }

        with patch("app.get_db_connection", return_value=KoneksiKosong()):
            for _ in range(5):
                respons = client.post("/", data=data)
                self.assertEqual(respons.status_code, 200)

            respons = client.post("/", data=data)

        self.assertEqual(respons.status_code, 429)
        self.assertIn(b"Terlalu banyak", respons.data)


class PasswordMinimalTest(unittest.TestCase):
    def setUp(self):
        modul_app._percobaan_login.clear()

    def test_password_kurang_dari_8_karakter_ditolak(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session["_csrf_token"] = TOKEN

        with patch("app.get_db_connection", return_value=KoneksiKosong()):
            respons = client.post("/register", data={
                "nama_lengkap": "Budi",
                "username": "budi",
                "password": "1234567",
                "konfirmasi_password": "1234567",
                "csrf_token": TOKEN,
            })

        self.assertEqual(respons.status_code, 200)
        self.assertIn(b"minimal 8 karakter", respons.data)


if __name__ == "__main__":
    unittest.main()
