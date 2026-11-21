import unittest
from unittest import mock
from unittest.mock import MagicMock

from database import MIGRASI, VERSI_SKEMA, buat_tabel


class KoneksiPalsu:
    def __init__(self, versi_awal=None):
        self.versi_awal = versi_awal
        self.dijalankan = []
        self.komit = 0
        self.rollback = 0

    def execute(self, sql, params=()):
        self.dijalankan.append((sql, params))
        kueri = " ".join(sql.split()).upper()

        if kueri.startswith("SELECT VERSI FROM SCHEMA_META"):
            baris = {"versi": self.versi_awal} if self.versi_awal else None
            return MagicMock(fetchone=lambda: baris)

        return MagicMock(fetchone=lambda: None)

    def commit(self):
        self.komit += 1

    def rollback(self):
        self.rollback += 1

    def close(self):
        pass


class MigrasiSkemaTest(unittest.TestCase):
    def test_db_baru_menjalankan_baseline_dan_mencatat_versi(self):
        palsu = KoneksiPalsu(versi_awal=None)

        with mock.patch(
            "database.get_db_connection", return_value=palsu
        ):
            buat_tabel()

        ada_baseline = any(
            "CREATE TABLE IF NOT EXISTS guru" in sql for sql, _ in palsu.dijalankan
        )
        ada_tutup_terkunci = any(
            "UPDATE periode_akademik" in sql for sql, _ in palsu.dijalankan
        )
        versi_disimpan = any(
            "INSERT INTO schema_meta (versi)" in sql for sql, _ in palsu.dijalankan
        )

        self.assertTrue(ada_baseline)
        self.assertTrue(ada_tutup_terkunci)
        self.assertTrue(versi_disimpan)
        self.assertEqual(palsu.komit, 1)

    def test_db_versi_terkini_tidak_menjalankan_migrasi(self):
        palsu = KoneksiPalsu(versi_awal=VERSI_SKEMA)

        with mock.patch(
            "database.get_db_connection", return_value=palsu
        ):
            buat_tabel()

        ada_ddl = any("CREATE TABLE IF NOT EXISTS guru" in sql for sql, _ in palsu.dijalankan)
        self.assertFalse(ada_ddl)
        self.assertEqual(palsu.komit, 1)

    def test_db_versi_2_menjalankan_migrasi_baru(self):
        if VERSI_SKEMA <= 2:
            self.skipTest("VERSI_SKEMA belum melewati 2.")

        palsu = KoneksiPalsu(versi_awal=2)
        dipanggil = []

        def catat(conn):
            dipanggil.append(True)

        migrasi_palsu = [(VERSI_SKEMA, catat)]

        with mock.patch("database.get_db_connection", return_value=palsu), \
             mock.patch("database.MIGRASI", migrasi_palsu):
            buat_tabel()

        self.assertEqual(dipanggil, [True])


if __name__ == "__main__":
    unittest.main()
