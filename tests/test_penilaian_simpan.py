import unittest
from unittest.mock import patch

from app import app, halaman_penilaian


class RekapitulasiHadirTest(unittest.TestCase):
    def test_tabel_hasil_memiliki_kolom_kehadiran(self):
        with app.test_request_context():
            from flask import render_template

            html = render_template(
                "rekapitulasi/rekapitulasi.html",
                hasil_rekap=[{
                    "no_absen": 1,
                    "nama_siswa": "Siswa Uji",
                    "nilai_tugas": 80,
                    "nilai_uts": 81,
                    "nilai_uas": 82,
                    "nilai_kehadiran": 83,
                    "nilai_akhir": 81.5,
                    "grade": "A",
                    "status_kkm": "Tuntas",
                }],
                jumlah_siswa=1,
                kelas_info={"nama_kelas": "Uji"},
                periode={"semester": "Ganjil", "tahun_ajaran": "2026"},
                daftar_periode_kelas=[],
                mapel_nama="Uji",
                kelas_nama="Uji",
                rata_rata_kelas=81.5,
                kategori_rata_rata="A",
                siswa_tertinggi=None,
                siswa_terendah=None,
                kkm=75,
                jumlah_tuntas=1,
                jumlah_belum_tuntas=0,
                jumlah_belum_lengkap=0,
                persen_tuntas=100,
                persen_belum_tuntas=0,
                distribusi_grade={"A": 1, "B": 0, "C": 0, "D": 0, "E": 0},
                distribusi_rentang={
                    "0-49": 0, "50-59": 0, "60-69": 0,
                    "70-79": 0, "80-89": 1, "90-100": 0,
                },
                rata_tugas=80,
                rata_uts=81,
                rata_uas=82,
            )

        self.assertIn("<th style=\"text-align: center;\">Kehadiran</th>", html)
        self.assertIn("83,00", html)


class SimpanNilaiBatchTest(unittest.TestCase):
    def test_upsert_nilai_satu_per_satu_dijalankan_dalam_batch(self):
        class Hasil:
            def __init__(self, baris=()):
                self.baris = baris

            def fetchall(self):
                return self.baris

            def fetchone(self):
                return self.baris[0] if self.baris else None

        class KoneksiUji:
            def __init__(self):
                self.batch = []
                self.jumlah_upsert_individual = 0

            def execute(self, sql, params=()):
                if "FROM pengaturan_penilaian" in sql:
                    return Hasil([{"bobot_sudah_diatur": 1, "grade_sudah_diatur": 1,
                                   "metode_tugas": "satu_per_satu"}])
                if "FROM periode_siswa ps" in sql and "JOIN siswa s" in sql:
                    return Hasil([
                        {"id_periode_siswa": 10, "nama_siswa": "Satu"},
                        {"id_periode_siswa": 11, "nama_siswa": "Dua"},
                    ])
                if "FROM tugas" in sql and "SELECT id_tugas" in sql:
                    return Hasil([{"id_tugas": 21}, {"id_tugas": 22}])
                if "FROM nilai_tugas nt" in sql:
                    return Hasil()
                if "FROM nilai_tugas_rata_rata" in sql or "FROM nilai_komponen nk" in sql:
                    return Hasil()
                return Hasil()

            def executemany(self, sql, rows):
                self.batch.append((sql, list(rows)))
                return Hasil()

            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        koneksi = KoneksiUji()
        periode = {"id_periode": 7, "status_terkunci": 0}

        with app.test_request_context(
            "/penilaian",
            method="POST",
            data={
                "aksi": "simpan_nilai",
                "csrf_token": "token-uji",
                "tugas_10_21": "80", "tugas_10_22": "81",
                "tugas_11_21": "82", "tugas_11_22": "83",
                "uts_10": "", "uas_10": "", "kehadiran_10": "",
                "uts_11": "", "uas_11": "", "kehadiran_11": "",
            },
        ):
            with app.test_request_context():
                from flask import session
                session["id_guru"] = 1
            with patch("app.session", {"id_guru": 1}), \
                 patch("app.ambil_periode", return_value=periode), \
                 patch("app.get_db_connection", return_value=koneksi), \
                 patch("app.catat_riwayat"):
                response = halaman_penilaian()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(koneksi.jumlah_upsert_individual, 0)
        self.assertEqual(len(koneksi.batch), 1)
        self.assertEqual(koneksi.batch[0][1], [
            (21, 10, 80.0), (22, 10, 81.0),
            (21, 11, 82.0), (22, 11, 83.0),
        ])


if __name__ == "__main__":
    unittest.main()
