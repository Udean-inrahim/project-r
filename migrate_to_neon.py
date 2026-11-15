"""Menyalin data dari database SQLite lokal ke Neon (Postgres).

Contoh pemakaian:
    python migrate_to_neon.py
    python migrate_to_neon.py --truncate
    python migrate_to_neon.py --sqlite instance/grade_master_v2.db
"""

import argparse
import sqlite3
import sys
from pathlib import Path

from database import get_db_connection

BASE_DIR = Path(__file__).resolve().parent
SQLITE_DEFAULT = BASE_DIR / "instance" / "grade_master_v2.db"

# Urutan mengikuti relasi foreign key.
URUTAN_TABEL = [
    "guru",
    "mata_pelajaran",
    "kelas",
    "periode_akademik",
    "siswa",
    "periode_siswa",
    "pengaturan_penilaian",
    "tugas",
    "nilai_tugas",
    "nilai_tugas_rata_rata",
    "nilai_komponen",
    "riwayat_perubahan",
]


def kolom_sqlite(conn, tabel):
    return [baris[1] for baris in conn.execute(f"PRAGMA table_info({tabel})")]


def kolom_postgres(conn, tabel):
    baris = conn.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = ?
    """, (tabel,)).fetchall()
    return {baris["column_name"] for baris in baris}


def kosongkan_postgres(conn):
    daftar = ", ".join(URUTAN_TABEL)
    conn.execute(f"TRUNCATE TABLE {daftar} RESTART IDENTITY CASCADE")


def reset_sequence(conn, tabel):
    kolom = conn.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = ?
          AND is_identity = 'YES'
    """, (tabel,)).fetchall()

    for baris in kolom:
        nama = baris["column_name"]
        conn.execute("""
            SELECT setval(
                pg_get_serial_sequence(?, ?),
                GREATEST(COALESCE(MAX(""" + nama + """), 0), 1),
                COALESCE(MAX(""" + nama + """), 0) > 0
            )
            FROM """ + tabel + """
        """, (tabel, nama))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", default=str(SQLITE_DEFAULT))
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Hapus seluruh data di Neon sebelum menyalin.",
    )
    args = parser.parse_args()

    sumber_path = Path(args.sqlite)
    if not sumber_path.exists():
        sys.exit(f"Database SQLite tidak ditemukan: {sumber_path}")

    sumber = sqlite3.connect(sumber_path)
    sumber.row_factory = sqlite3.Row
    tujuan = get_db_connection()

    try:
        jumlah_guru = tujuan.execute(
            "SELECT COUNT(*) AS jumlah FROM guru"
        ).fetchone()["jumlah"]

        if args.truncate:
            kosongkan_postgres(tujuan)
        elif jumlah_guru:
            sys.exit(
                "Neon sudah berisi data guru. Jalankan dengan --truncate "
                "kalau memang ingin menimpanya."
            )

        for tabel in URUTAN_TABEL:
            kolom = [k for k in kolom_sqlite(sumber, tabel)
                     if k in kolom_postgres(tujuan, tabel)]
            baris_sumber = sumber.execute(
                f"SELECT {', '.join(kolom)} FROM {tabel}"
            ).fetchall()

            if not baris_sumber:
                print(f"{tabel:24} 0 baris")
                continue

            tempat = ", ".join(["?"] * len(kolom))
            daftar_kolom = ", ".join(kolom)
            sql = (
                f"INSERT INTO {tabel} ({daftar_kolom}) "
                f"VALUES ({tempat})"
            )

            for baris in baris_sumber:
                tujuan.execute(sql, tuple(baris[k] for k in kolom))

            print(f"{tabel:24} {len(baris_sumber)} baris")

        for tabel in URUTAN_TABEL:
            reset_sequence(tujuan, tabel)

        tujuan.commit()
        print("\nMigrasi selesai.")

    except Exception:
        tujuan.rollback()
        raise

    finally:
        tujuan.close()
        sumber.close()


if __name__ == "__main__":
    main()
