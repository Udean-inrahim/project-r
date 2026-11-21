import unittest
from unittest.mock import patch

from app import app, get_db_connection


class KoneksiPalsu:
    def __init__(self):
        self.ditutup = 0
        self.dirollback = 0

    def execute(self, *args, **kwargs):
        return self

    def fetchone(self):
        return (1,)

    def commit(self):
        pass

    def rollback(self):
        self.dirollback += 1

    def close(self):
        self.ditutup += 1


class KoneksiPerRequestTest(unittest.TestCase):
    def test_satu_koneksi_dipakai_ulang_dalam_request(self):
        palsu = KoneksiPalsu()

        with patch("app.buka_koneksi_baru", return_value=palsu) as buka:
            with app.app_context():
                pertama = get_db_connection()
                kedua = get_db_connection()
                pertama.execute("SELECT 1")

        buka.assert_called_once()
        self.assertIs(pertama._koneksi, kedua._koneksi)
        self.assertEqual(palsu.ditutup, 1)

    def test_close_di_tengah_request_tidak_menutup_koneksi(self):
        palsu = KoneksiPalsu()

        with patch("app.buka_koneksi_baru", return_value=palsu):
            with app.app_context():
                koneksi = get_db_connection()
                koneksi.close()
                self.assertEqual(palsu.ditutup, 0)
                self.assertEqual(palsu.dirollback, 1)

                get_db_connection().execute("SELECT 1")

        self.assertEqual(palsu.ditutup, 1)

    def test_di_luar_request_tidak_berbagi_koneksi(self):
        with patch(
            "app.buka_koneksi_baru", side_effect=KoneksiPalsu
        ) as buka:
            pertama = get_db_connection()
            kedua = get_db_connection()

        self.assertEqual(buka.call_count, 2)
        self.assertIsNot(pertama, kedua)
        pertama.close()
        kedua.close()

    def test_teardown_request_menutup_koneksi(self):
        palsu = KoneksiPalsu()
        client = app.test_client()

        with patch("app.buka_koneksi_baru", return_value=palsu):
            respons = client.get("/healthz")

        self.assertEqual(respons.status_code, 200)
        self.assertEqual(palsu.ditutup, 1)


if __name__ == "__main__":
    unittest.main()
