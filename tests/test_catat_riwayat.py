import os
import unittest
import sqlite3

os.environ.setdefault("SECRET_KEY", "test-secret-key")

from app import catat_riwayat
from database import ada_nilai_tersimpan


class CatatRiwayatTest(unittest.TestCase):
    def test_mencatat_riwayat_tanpa_mengecek_nilai_tersimpan(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE riwayat_perubahan (
                id_guru INTEGER,
                id_periode INTEGER,
                id_periode_siswa INTEGER,
                jenis_perubahan TEXT,
                keterangan TEXT,
                nilai_lama TEXT,
                nilai_baru TEXT
            )
        """)

        catat_riwayat(conn, 1, 2, 3, "ubah_nilai_siswa", "Nilai diubah")

        jumlah = conn.execute(
            "SELECT COUNT(*) FROM riwayat_perubahan"
        ).fetchone()[0]
        self.assertEqual(jumlah, 1)
        conn.close()

    def test_mendeteksi_nilai_tugas_yang_terhubung_ke_siswa_periode(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript("""
            CREATE TABLE periode_akademik (id_periode INTEGER, id_kelas INTEGER);
            CREATE TABLE periode_siswa (id_periode_siswa INTEGER, id_periode INTEGER);
            CREATE TABLE nilai_tugas (id_periode_siswa INTEGER, nilai REAL);
            CREATE TABLE nilai_tugas_rata_rata (id_periode_siswa INTEGER, nilai REAL);
            CREATE TABLE nilai_komponen (
                id_periode_siswa INTEGER,
                nilai_uts REAL,
                nilai_uas REAL,
                nilai_kehadiran REAL
            );
            INSERT INTO periode_akademik VALUES (2, 20);
            INSERT INTO periode_siswa VALUES (3, 2);
            INSERT INTO nilai_tugas VALUES (3, 88);
        """)

        self.assertTrue(ada_nilai_tersimpan(conn, "p.id_periode = ?", (2,)))
        conn.close()


if __name__ == "__main__":
    unittest.main()
