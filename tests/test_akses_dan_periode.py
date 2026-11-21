import unittest
from unittest.mock import patch

from app import app

TOKEN = "token-uji-csrf"

HALAMAN_WAJIB_LOGIN = [
    "/dashboard",
    "/penilaian",
    "/rekapitulasi",
    "/kelas/kelola",
    "/siswa",
    "/api/search",
    "/penilaian/pengaturan",
]


class KoneksiPengaturan:
    def __init__(self):
        self.pengaturan = {
            "bobot_sudah_diatur": 1,
            "grade_sudah_diatur": 1,
            "metode_tugas": "satu_per_satu",
        }

    def execute(self, *args, **kwargs):
        return self

    def fetchone(self):
        return self.pengaturan

    def close(self):
        pass


class WajibLoginTest(unittest.TestCase):
    def test_halaman_utama_redirect_ke_login(self):
        client = app.test_client()

        for path in HALAMAN_WAJIB_LOGIN:
            with self.subTest(path=path):
                respons = client.get(path)
                self.assertEqual(respons.status_code, 302)
                self.assertIn("/", respons.headers["Location"])


class PeriodeTerkunciTest(unittest.TestCase):
    def test_post_ditolak_saat_periode_terkunci(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session["id_guru"] = 1
            session["id_periode"] = 10
            session["id_mata_pelajaran"] = 20
            session["_csrf_token"] = TOKEN

        with patch(
            "app.ambil_periode",
            return_value={"status_terkunci": 1},
        ), patch(
            "app.get_db_connection",
            return_value=KoneksiPengaturan(),
        ):
            respons = client.post("/penilaian", data={
                "aksi": "atur_jumlah_tugas",
                "jumlah_tugas": "2",
                "csrf_token": TOKEN,
            })

        self.assertEqual(respons.status_code, 302)
        with client.session_transaction() as session:
            pesan = " ".join(
                teks for _, teks in session.get("_flashes", [])
            )
        self.assertIn("terkunci", pesan.lower())


if __name__ == "__main__":
    unittest.main()
