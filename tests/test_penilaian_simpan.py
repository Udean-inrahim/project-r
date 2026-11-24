import unittest

from app import app


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


if __name__ == "__main__":
    unittest.main()
