import sqlite3
from pathlib import Path

# Lokasi database
BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
DATABASE_PATH = INSTANCE_DIR / "grade_master_v2.db"


def get_db_connection():
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    return conn


def buat_tabel():
    conn = get_db_connection()

    try:
        cursor = conn.cursor()

        # 1. Akun guru
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS guru (
                id_guru INTEGER PRIMARY KEY AUTOINCREMENT,
                nama_lengkap TEXT NOT NULL,
                username TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                dibuat_pada TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Migrasi: menyimpan mata pelajaran terakhir yang digunakan
        kolom_guru = [
            kolom["name"]
            for kolom in cursor.execute(
                "PRAGMA table_info(guru)"
            ).fetchall()
        ]

        if "id_mata_pelajaran_terakhir" not in kolom_guru:
            cursor.execute("""
                ALTER TABLE guru
                ADD COLUMN id_mata_pelajaran_terakhir INTEGER
            """)

        # 2. Mata pelajaran
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mata_pelajaran (
                id_mata_pelajaran INTEGER PRIMARY KEY AUTOINCREMENT,
                id_guru INTEGER NOT NULL,
                nama_mata_pelajaran TEXT NOT NULL,
                status_arsip INTEGER NOT NULL DEFAULT 0,
                dibuat_pada TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (id_guru)
                    REFERENCES guru(id_guru)
                    ON DELETE CASCADE,

                UNIQUE (id_guru, nama_mata_pelajaran)
            )
        """)

        # Migrasi: menambahkan status_arsip
        kolom_mata_pelajaran = [
            kolom["name"]
            for kolom in cursor.execute(
                "PRAGMA table_info(mata_pelajaran)"
            ).fetchall()
        ]

        if "status_arsip" not in kolom_mata_pelajaran:
            cursor.execute("""
                ALTER TABLE mata_pelajaran
                ADD COLUMN status_arsip INTEGER NOT NULL DEFAULT 0
            """)

        # 3. Kelas
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS kelas (
                id_kelas INTEGER PRIMARY KEY AUTOINCREMENT,
                id_mata_pelajaran INTEGER NOT NULL,
                nama_kelas TEXT NOT NULL,
                status_arsip INTEGER NOT NULL DEFAULT 0,
                dibuat_pada TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (id_mata_pelajaran)
                    REFERENCES mata_pelajaran(id_mata_pelajaran)
                    ON DELETE CASCADE,

                UNIQUE (id_mata_pelajaran, nama_kelas)
            )
        """)

        # Migrasi: menambahkan status_arsip
        kolom_kelas = [
            kolom["name"]
            for kolom in cursor.execute(
                "PRAGMA table_info(kelas)"
            ).fetchall()
        ]

        if "status_arsip" not in kolom_kelas:
            cursor.execute("""
                ALTER TABLE kelas
                ADD COLUMN status_arsip INTEGER NOT NULL DEFAULT 0
            """)

        # 4. Periode akademik
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS periode_akademik (
                id_periode INTEGER PRIMARY KEY AUTOINCREMENT,
                id_kelas INTEGER NOT NULL,
                tahun_ajaran TEXT NOT NULL,
                semester TEXT NOT NULL
                    CHECK (semester IN ('Ganjil', 'Genap')),
                status_aktif INTEGER NOT NULL DEFAULT 0,
                status_terkunci INTEGER NOT NULL DEFAULT 0,
                dibuat_pada TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (id_kelas)
                    REFERENCES kelas(id_kelas)
                    ON DELETE CASCADE,

                UNIQUE (id_kelas, tahun_ajaran, semester)
            )
        """)

        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
            satu_periode_aktif_per_kelas
            ON periode_akademik(id_kelas)
            WHERE status_aktif = 1
        """)

        # Semua periode yang tidak aktif dianggap terkunci.
        cursor.execute("""
            UPDATE periode_akademik
            SET status_terkunci = 1
            WHERE status_aktif = 0
        """)

        # 5. Identitas siswa
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS siswa (
                id_siswa INTEGER PRIMARY KEY AUTOINCREMENT,
                id_guru INTEGER NOT NULL,
                nama_siswa TEXT NOT NULL,
                nomor_identitas TEXT,
                status_arsip INTEGER NOT NULL DEFAULT 0,
                dibuat_pada TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (id_guru)
                    REFERENCES guru(id_guru)
                    ON DELETE CASCADE,

                UNIQUE (id_guru, nomor_identitas)
            )
        """)

        # 6. Siswa dalam setiap periode
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS periode_siswa (
                id_periode_siswa INTEGER PRIMARY KEY AUTOINCREMENT,
                id_periode INTEGER NOT NULL,
                id_siswa INTEGER NOT NULL,
                no_absen INTEGER NOT NULL,
                status_aktif INTEGER NOT NULL DEFAULT 1,
                dibuat_pada TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (id_periode)
                    REFERENCES periode_akademik(id_periode)
                    ON DELETE CASCADE,

                FOREIGN KEY (id_siswa)
                    REFERENCES siswa(id_siswa)
                    ON DELETE RESTRICT,

                UNIQUE (id_periode, id_siswa)
            )
        """)

        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
            satu_no_absen_aktif_per_periode
            ON periode_siswa(id_periode, no_absen)
            WHERE status_aktif = 1
        """)

        # 7. Pengaturan penilaian
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pengaturan_penilaian (
                id_pengaturan INTEGER PRIMARY KEY AUTOINCREMENT,
                id_periode INTEGER NOT NULL UNIQUE,

                bobot_sudah_diatur INTEGER NOT NULL DEFAULT 0,
                grade_sudah_diatur INTEGER NOT NULL DEFAULT 0,

                bobot_tugas REAL NOT NULL DEFAULT 30,
                bobot_uts REAL NOT NULL DEFAULT 25,
                bobot_uas REAL NOT NULL DEFAULT 30,
                bobot_kehadiran REAL NOT NULL DEFAULT 15,

                metode_tugas TEXT NOT NULL DEFAULT 'satu_per_satu'
                    CHECK (
                        metode_tugas IN (
                            'satu_per_satu',
                            'langsung_rata_rata'
                        )
                    ),

                jumlah_tugas INTEGER NOT NULL DEFAULT 0,

                batas_a REAL NOT NULL DEFAULT 85,
                batas_b REAL NOT NULL DEFAULT 75,
                batas_c REAL NOT NULL DEFAULT 65,
                batas_d REAL NOT NULL DEFAULT 50,
                batas_e REAL NOT NULL DEFAULT 0,

                kkm REAL NOT NULL DEFAULT 75,
                dibuat_pada TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (id_periode)
                    REFERENCES periode_akademik(id_periode)
                    ON DELETE CASCADE
            )
        """)

        # 8. Daftar tugas
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tugas (
                id_tugas INTEGER PRIMARY KEY AUTOINCREMENT,
                id_periode INTEGER NOT NULL,
                nama_tugas TEXT NOT NULL,
                deskripsi TEXT,
                tanggal_dibuat TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (id_periode)
                    REFERENCES periode_akademik(id_periode)
                    ON DELETE CASCADE
            )
        """)

        # Migrasi: menambahkan jumlah_tugas
        # untuk database lama yang sudah memiliki tabel pengaturan.
        kolom_pengaturan = [
            row["name"]
            for row in cursor.execute(
                "PRAGMA table_info(pengaturan_penilaian)"
            ).fetchall()
        ]

        if "jumlah_tugas" not in kolom_pengaturan:
            cursor.execute("""
                ALTER TABLE pengaturan_penilaian
                ADD COLUMN jumlah_tugas INTEGER NOT NULL DEFAULT 0
            """)

            # Menyesuaikan jumlah tugas dengan data lama.
            cursor.execute("""
                UPDATE pengaturan_penilaian
                SET jumlah_tugas = (
                    SELECT COUNT(*)
                    FROM tugas
                    WHERE tugas.id_periode =
                          pengaturan_penilaian.id_periode
                )
            """)

        # 9. Nilai setiap tugas
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS nilai_tugas (
                id_nilai_tugas INTEGER PRIMARY KEY AUTOINCREMENT,
                id_tugas INTEGER NOT NULL,
                id_periode_siswa INTEGER NOT NULL,

                nilai REAL CHECK (
                    nilai IS NULL OR (nilai >= 0 AND nilai <= 100)
                ),

                diperbarui_pada TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (id_tugas)
                    REFERENCES tugas(id_tugas)
                    ON DELETE CASCADE,

                FOREIGN KEY (id_periode_siswa)
                    REFERENCES periode_siswa(id_periode_siswa)
                    ON DELETE CASCADE,

                UNIQUE (id_tugas, id_periode_siswa)
            )
        """)

        # 10. Nilai tugas metode rata-rata
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS nilai_tugas_rata_rata (
                id_nilai_rata_rata INTEGER PRIMARY KEY AUTOINCREMENT,
                id_periode_siswa INTEGER NOT NULL UNIQUE,

                nilai REAL CHECK (
                    nilai IS NULL OR (nilai >= 0 AND nilai <= 100)
                ),

                diperbarui_pada TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (id_periode_siswa)
                    REFERENCES periode_siswa(id_periode_siswa)
                    ON DELETE CASCADE
            )
        """)

        # 11. Nilai UTS, UAS, dan kehadiran
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS nilai_komponen (
                id_nilai_komponen INTEGER PRIMARY KEY AUTOINCREMENT,
                id_periode_siswa INTEGER NOT NULL UNIQUE,

                nilai_uts REAL CHECK (
                    nilai_uts IS NULL OR
                    (nilai_uts >= 0 AND nilai_uts <= 100)
                ),

                nilai_uas REAL CHECK (
                    nilai_uas IS NULL OR
                    (nilai_uas >= 0 AND nilai_uas <= 100)
                ),

                nilai_kehadiran REAL CHECK (
                    nilai_kehadiran IS NULL OR
                    (nilai_kehadiran >= 0 AND nilai_kehadiran <= 100)
                ),

                diperbarui_pada TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (id_periode_siswa)
                    REFERENCES periode_siswa(id_periode_siswa)
                    ON DELETE CASCADE
            )
        """)

        # 12. Riwayat perubahan
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS riwayat_perubahan (
                id_riwayat INTEGER PRIMARY KEY AUTOINCREMENT,
                id_guru INTEGER,
                id_periode INTEGER,
                id_periode_siswa INTEGER,

                jenis_perubahan TEXT NOT NULL,
                keterangan TEXT NOT NULL,
                nilai_lama TEXT,
                nilai_baru TEXT,

                waktu_perubahan TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (id_guru)
                    REFERENCES guru(id_guru)
                    ON DELETE SET NULL,

                FOREIGN KEY (id_periode)
                    REFERENCES periode_akademik(id_periode)
                    ON DELETE SET NULL,

                FOREIGN KEY (id_periode_siswa)
                    REFERENCES periode_siswa(id_periode_siswa)
                    ON DELETE SET NULL
            )
        """)

        # 13. Indeks pencarian
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_mata_pelajaran_guru
            ON mata_pelajaran(id_guru)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_kelas_mata_pelajaran
            ON kelas(id_mata_pelajaran)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_periode_kelas
            ON periode_akademik(id_kelas)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_siswa_nama
            ON siswa(nama_siswa)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_periode_siswa_absen
            ON periode_siswa(id_periode, no_absen)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_tugas_periode
            ON tugas(id_periode)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_riwayat_periode
            ON riwayat_perubahan(id_periode)
        """)

        # Migrasi: menambahkan penanda pengaturan bobot.
        kolom_pengaturan = [
            row["name"]
            for row in cursor.execute(
                "PRAGMA table_info(pengaturan_penilaian)"
            ).fetchall()
        ]

        if "bobot_sudah_diatur" not in kolom_pengaturan:
            cursor.execute("""
                ALTER TABLE pengaturan_penilaian
                ADD COLUMN bobot_sudah_diatur INTEGER NOT NULL DEFAULT 0
            """)

        # Migrasi: menambahkan penanda pengaturan grade.
        kolom_pengaturan = [
            row["name"]
            for row in cursor.execute(
                "PRAGMA table_info(pengaturan_penilaian)"
            ).fetchall()
        ]

        if "grade_sudah_diatur" not in kolom_pengaturan:
            cursor.execute("""
                ALTER TABLE pengaturan_penilaian
                ADD COLUMN grade_sudah_diatur INTEGER NOT NULL DEFAULT 0
            """)

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    buat_tabel()
    print("Database GRADE MASTER V2 berhasil dibuat!")