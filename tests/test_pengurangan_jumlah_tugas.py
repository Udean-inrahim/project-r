import sqlite3
import unittest

from app import app, kurangi_jumlah_tugas

class PenguranganJumlahTugasTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE tugas (id_tugas INTEGER PRIMARY KEY, id_periode INTEGER);
            CREATE TABLE nilai_tugas (id_tugas INTEGER, nilai REAL);
            CREATE TABLE pengaturan_penilaian (id_periode INTEGER, jumlah_tugas INTEGER);
            INSERT INTO tugas VALUES (11, 10), (12, 10), (13, 10);
            INSERT INTO pengaturan_penilaian VALUES (10, 3);
        """)

    def tearDown(self):
        self.conn.close()

    def test_menolak_pengurangan_jika_nilai_tersimpan(self):
        self.conn.execute("INSERT INTO nilai_tugas VALUES (13, 82)")
        self.assertFalse(self.kurangi())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tugas").fetchone()[0], 3)
        self.assertEqual(self.conn.execute("SELECT jumlah_tugas FROM pengaturan_penilaian").fetchone()[0], 3)

    def test_menghapus_tugas_tertinggi_jika_nilainya_kosong(self):
        self.assertTrue(self.kurangi())
        self.assertEqual([row[0] for row in self.conn.execute("SELECT id_tugas FROM tugas ORDER BY id_tugas").fetchall()], [11, 12])
        self.assertEqual(self.conn.execute("SELECT jumlah_tugas FROM pengaturan_penilaian").fetchone()[0], 2)

    def test_form_mengizinkan_jumlah_di_bawah_jumlah_saat_ini(self):
        with app.test_request_context():
            from flask import render_template
            html = render_template(
                "penilaian/penilaian.html",
                periode={"status_terkunci": 0, "tahun_ajaran": "2026", "semester": "Ganjil"},
                pengaturan={"metode_tugas": "satu_per_satu"},
                daftar_tugas=[{}, {}, {}], daftar_siswa=[], nilai_tugas={}, hasil_nilai={},
                nilai_rata_rata={}, nilai_komponen={},
            )
        self.assertIn('name="jumlah_tugas"', html)
        self.assertIn('min="1"', html)

    def kurangi(self):
        class PostgresQueryAdapter:
            def execute(self, sql, params=()):
                if "BEGIN" in sql:
                    return self.conn.execute("SELECT 1")
                if "FOR UPDATE" in sql:
                    return self.conn.execute(
                        "SELECT id_tugas FROM tugas WHERE id_periode = ? "
                        "ORDER BY id_tugas DESC LIMIT ?", params
                    )
                if "ANY(?::integer[])" in sql:
                    marks = ",".join("?" for _ in params[0])
                    sql = sql.replace("id_tugas = ANY(?::integer[])", f"id_tugas IN ({marks})")
                    params = params[0]
                return self.conn.execute(sql.replace("?", "?"), params)

            def __init__(self, conn):
                self.conn = conn

            def commit(self):
                self.conn.commit()

            def rollback(self):
                self.conn.rollback()

        return kurangi_jumlah_tugas(PostgresQueryAdapter(self.conn), 10, 3, 2)


if __name__ == "__main__":
    unittest.main()
