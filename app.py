import os
from datetime import date
from functools import wraps

from flask import (
    Flask, render_template, request,
    redirect, url_for, session, flash,
    jsonify
)
from werkzeug.security import (
    generate_password_hash, check_password_hash
)

from database import IntegrityError, buat_tabel, get_db_connection


def ambil_secret_key():
    kunci = os.environ.get("SECRET_KEY", "").strip()
    if not kunci:
        raise RuntimeError(
            "SECRET_KEY belum diatur. Isi environment variable SECRET_KEY "
            "atau berkas .env."
        )
    return kunci


app = Flask(__name__)
app.secret_key = ambil_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("VERCEL") == "1",
)

# Jangan biarkan kegagalan database saat import mematikan seluruh aplikasi.
# Kesalahannya disimpan supaya bisa diperiksa lewat /healthz.
galat_db_awal = None

try:
    buat_tabel()
except Exception as kesalahan:  # noqa: BLE001
    galat_db_awal = kesalahan


# ==================================================
# FUNGSI BANTU
# ==================================================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "id_guru" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


def kurangi_jumlah_tugas(conn, id_periode, jumlah_lama, jumlah_baru):
    if jumlah_baru < 1 or jumlah_baru >= jumlah_lama:
        return False

    conn.execute("BEGIN")
    tugas_dihapus = conn.execute("""
        SELECT id_tugas
        FROM tugas
        WHERE id_periode = ?
        ORDER BY id_tugas DESC
        LIMIT ?
        FOR UPDATE
    """, (id_periode, jumlah_lama - jumlah_baru)).fetchall()
    id_tugas_dihapus = [tugas["id_tugas"] for tugas in tugas_dihapus]
    if len(id_tugas_dihapus) != jumlah_lama - jumlah_baru:
        conn.rollback()
        return False

    nilai_tersimpan = conn.execute("""
        SELECT 1
        FROM nilai_tugas
        WHERE id_tugas = ANY(?::integer[])
          AND nilai IS NOT NULL
        LIMIT 1
    """, (id_tugas_dihapus,)).fetchone()
    if nilai_tersimpan:
        conn.rollback()
        return False

    for id_tugas in id_tugas_dihapus:
        conn.execute("""
            DELETE FROM tugas
            WHERE id_tugas = ? AND id_periode = ?
        """, (id_tugas, id_periode))
    conn.execute("""
        UPDATE pengaturan_penilaian
        SET jumlah_tugas = ?
        WHERE id_periode = ?
    """, (jumlah_baru, id_periode))
    conn.commit()
    return True


def ambil_mata_pelajaran(id_mata_pelajaran):
    if not id_mata_pelajaran:
        return None

    conn = get_db_connection()
    data = conn.execute("""
        SELECT *
        FROM mata_pelajaran
        WHERE id_mata_pelajaran = ?
          AND id_guru = ?
    """, (id_mata_pelajaran, session["id_guru"])).fetchone()
    conn.close()
    return data


@app.context_processor
def inject_global_sidebar_data():
    id_guru = session.get("id_guru")
    if not id_guru:
        return {}
    try:
        conn = get_db_connection()
        id_periode = session.get("id_periode")
        id_kelas = session.get("id_kelas")
        id_mp = session.get("id_mata_pelajaran")

        mapel_nama = ""
        kelas_nama = ""
        periode_data = None

        if id_periode:
            p = conn.execute("""
                SELECT p.*, k.nama_kelas, m.nama_mata_pelajaran
                FROM periode_akademik p
                JOIN kelas k ON p.id_kelas = k.id_kelas
                JOIN mata_pelajaran m ON k.id_mata_pelajaran = m.id_mata_pelajaran
                WHERE p.id_periode = ? AND m.id_guru = ?
            """, (id_periode, id_guru)).fetchone()
            if p:
                periode_data = p
                mapel_nama = p["nama_mata_pelajaran"]
                kelas_nama = p["nama_kelas"]

        if not mapel_nama and id_mp:
            m = conn.execute("SELECT nama_mata_pelajaran FROM mata_pelajaran WHERE id_mata_pelajaran = ? AND id_guru = ?", (id_mp, id_guru)).fetchone()
            if m:
                mapel_nama = m["nama_mata_pelajaran"]

        if not kelas_nama and id_kelas:
            k = conn.execute("SELECT nama_kelas FROM kelas WHERE id_kelas = ?", (id_kelas,)).fetchone()
            if k:
                kelas_nama = k["nama_kelas"]

        conn.close()
        return {
            "global_nama_guru": session.get("nama_guru", "Guru"),
            "global_mapel_aktif": mapel_nama,
            "global_kelas_aktif": kelas_nama,
            "global_periode_aktif": periode_data
        }
    except Exception:
        return {
            "global_nama_guru": session.get("nama_guru", "Guru"),
            "global_mapel_aktif": "",
            "global_kelas_aktif": "",
            "global_periode_aktif": None
        }


def ambil_kelas(id_kelas, termasuk_arsip=False):
    if not id_kelas:
        return None

    conn = get_db_connection()

    kondisi_arsip = ""
    if not termasuk_arsip:
        kondisi_arsip = "AND k.status_arsip = 0"

    data = conn.execute(f"""
        SELECT k.*
        FROM kelas k
        JOIN mata_pelajaran m
            ON k.id_mata_pelajaran = m.id_mata_pelajaran
        WHERE k.id_kelas = ?
          AND m.id_guru = ?
          {kondisi_arsip}
    """, (id_kelas, session["id_guru"])).fetchone()

    conn.close()
    return data


def ambil_periode(id_periode):
    if not id_periode:
        return None

    conn = get_db_connection()
    data = conn.execute("""
        SELECT p.*
        FROM periode_akademik p
        JOIN kelas k ON p.id_kelas = k.id_kelas
        JOIN mata_pelajaran m
            ON k.id_mata_pelajaran = m.id_mata_pelajaran
        WHERE p.id_periode = ?
          AND m.id_guru = ?
    """, (id_periode, session["id_guru"])).fetchone()
    conn.close()
    return data


def pastikan_periode_aktif():
    """
    Memastikan session memiliki id_periode yang valid milik guru yang login.
    Jika tidak ada atau tidak valid, coba cari atau buat secara otomatis:
    1. Cek periode dari id_periode di session.
    2. Cek periode aktif dari id_kelas di session (buat periode awal jika kelas belum punya).
    3. Cek periode aktif dari id_mata_pelajaran di session.
    4. Cek periode aktif apa pun milik guru.
    """
    id_guru = session.get("id_guru")
    if not id_guru:
        return None

    id_periode = session.get("id_periode")
    periode = ambil_periode(id_periode)
    if periode:
        return periode

    conn = get_db_connection()
    try:
        # Cek kelas aktif di session
        id_kelas = session.get("id_kelas")
        if id_kelas:
            k = conn.execute("""
                SELECT k.*, m.id_mata_pelajaran
                FROM kelas k
                JOIN mata_pelajaran m ON k.id_mata_pelajaran = m.id_mata_pelajaran
                WHERE k.id_kelas = ? AND m.id_guru = ? AND k.status_arsip = 0
            """, (id_kelas, id_guru)).fetchone()

            if k:
                p = conn.execute("""
                    SELECT * FROM periode_akademik
                    WHERE id_kelas = ?
                    ORDER BY status_aktif DESC, id_periode DESC
                    LIMIT 1
                """, (id_kelas,)).fetchone()

                if not p:
                    cursor = conn.execute("""
                        INSERT INTO periode_akademik (id_kelas, tahun_ajaran, semester, status_aktif, status_terkunci)
                        VALUES (?, '2026/2027', 'Ganjil', 1, 0)
                        RETURNING *
                    """, (id_kelas,))
                    p = cursor.fetchone()
                    conn.execute("""
                        INSERT INTO pengaturan_penilaian (
                            id_periode, bobot_sudah_diatur, grade_sudah_diatur,
                            bobot_tugas, bobot_uts, bobot_uas, bobot_kehadiran,
                            kkm, batas_a, batas_b, batas_c, batas_d, batas_e
                        )
                        VALUES (?, 1, 1, 30, 25, 30, 15, 75, 85, 75, 65, 50, 0)
                    """, (p["id_periode"],))
                    conn.commit()

                session["id_periode"] = p["id_periode"]
                session["id_kelas"] = p["id_kelas"]
                session["id_mata_pelajaran"] = k["id_mata_pelajaran"]
                return ambil_periode(p["id_periode"])

        # Cek berdasarkan id_mata_pelajaran di session
        id_mp = session.get("id_mata_pelajaran")
        if id_mp:
            p = conn.execute("""
                SELECT p.*, k.id_mata_pelajaran
                FROM periode_akademik p
                JOIN kelas k ON p.id_kelas = k.id_kelas
                JOIN mata_pelajaran m ON k.id_mata_pelajaran = m.id_mata_pelajaran
                WHERE m.id_mata_pelajaran = ? AND m.id_guru = ? AND k.status_arsip = 0
                ORDER BY p.status_aktif DESC, p.id_periode DESC
                LIMIT 1
            """, (id_mp, id_guru)).fetchone()

            if p:
                session["id_periode"] = p["id_periode"]
                session["id_kelas"] = p["id_kelas"]
                session["id_mata_pelajaran"] = p["id_mata_pelajaran"]
                return ambil_periode(p["id_periode"])

            # Jika mapel ini punya kelas tapi belum ada periode, buatkan periode
            k = conn.execute("""
                SELECT * FROM kelas
                WHERE id_mata_pelajaran = ? AND status_arsip = 0
                ORDER BY id_kelas ASC
                LIMIT 1
            """, (id_mp,)).fetchone()
            if k:
                cursor = conn.execute("""
                    INSERT INTO periode_akademik (id_kelas, tahun_ajaran, semester, status_aktif, status_terkunci)
                    VALUES (?, '2026/2027', 'Ganjil', 1, 0)
                    RETURNING *
                """, (k["id_kelas"],))
                p = cursor.fetchone()
                conn.execute("""
                    INSERT INTO pengaturan_penilaian (
                        id_periode, bobot_sudah_diatur, grade_sudah_diatur,
                        bobot_tugas, bobot_uts, bobot_uas, bobot_kehadiran,
                        kkm, batas_a, batas_b, batas_c, batas_d, batas_e
                    )
                    VALUES (?, 1, 1, 30, 25, 30, 15, 75, 85, 75, 65, 50, 0)
                """, (p["id_periode"],))
                conn.commit()

                session["id_periode"] = p["id_periode"]
                session["id_kelas"] = p["id_kelas"]
                session["id_mata_pelajaran"] = k["id_mata_pelajaran"]
                return ambil_periode(p["id_periode"])

        # Cek seluruh periode yang dimiliki guru
        p = conn.execute("""
            SELECT p.*, k.id_mata_pelajaran
            FROM periode_akademik p
            JOIN kelas k ON p.id_kelas = k.id_kelas
            JOIN mata_pelajaran m ON k.id_mata_pelajaran = m.id_mata_pelajaran
            WHERE m.id_guru = ? AND k.status_arsip = 0 AND m.status_arsip = 0
            ORDER BY p.status_aktif DESC, p.id_periode DESC
            LIMIT 1
        """, (id_guru,)).fetchone()

        if p:
            session["id_periode"] = p["id_periode"]
            session["id_kelas"] = p["id_kelas"]
            session["id_mata_pelajaran"] = p["id_mata_pelajaran"]
            return ambil_periode(p["id_periode"])

        return None
    finally:
        conn.close()


# ==================================================
# PEMERIKSAAN KESEHATAN APLIKASI
# ==================================================

def samarkan(teks):
    """Menyembunyikan kredensial dari pesan kesalahan."""
    teks = str(teks)
    if "@" in teks and "://" in teks:
        awal = teks.index("://") + 3
        akhir = teks.index("@")
        teks = teks[:awal] + "***" + teks[akhir:]
    return teks[:300]


@app.route("/healthz")
def healthz():
    from database import GALAT_PSYCOPG, ambil_database_url

    try:
        ambil_database_url()
        url_ada = True
    except Exception:
        url_ada = False

    hasil = {
        "psycopg": "ok" if GALAT_PSYCOPG is None else "gagal",
        "database_url": "terisi" if url_ada else "kosong",
        "skema_awal": "ok" if galat_db_awal is None else "gagal",
    }

    if GALAT_PSYCOPG is not None:
        hasil["galat_psycopg"] = samarkan(GALAT_PSYCOPG)

    if galat_db_awal is not None:
        hasil["galat_skema_awal"] = samarkan(galat_db_awal)

    try:
        conn = get_db_connection()
        jumlah = conn.execute(
            "SELECT COUNT(*) AS jumlah FROM guru"
        ).fetchone()["jumlah"]
        conn.close()
        hasil["database"] = "ok"
        hasil["jumlah_guru"] = jumlah
        kode = 200
    except Exception as kesalahan:  # noqa: BLE001
        hasil["database"] = "gagal"
        hasil["galat_database"] = samarkan(kesalahan)
        kode = 500

    return jsonify(hasil), kode


# ==================================================
# LOGIN DAN PENDAFTARAN
# ==================================================

@app.route("/", methods=["GET", "POST"])
def login():
    conn = get_db_connection()

    pesan = None

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        guru = conn.execute("""
            SELECT *
            FROM guru
            WHERE username = ?
        """, (username,)).fetchone()

        if guru and check_password_hash(guru["password"], password):
            session.clear()
            session["id_guru"] = guru["id_guru"]
            session["nama_guru"] = guru["nama_lengkap"]

            conn.close()
            return redirect(
                url_for("dashboard")
            )

        flash("Data login tidak sesuai. Silahkan periksa kembali.", "error")

    conn.close()

    return render_template("auth/login.html", pesan=pesan)


@app.route("/register", methods=["GET", "POST"])
def register():
    conn = get_db_connection()

    pesan = None

    if request.method == "POST":
        nama = request.form.get("nama_lengkap", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        konfirmasi = request.form.get("konfirmasi_password", "")

        if not nama or not username or not password:
            flash("Semua data wajib diisi.", "error")
        elif len(password) < 6:
            flash("Kata sandi harus terdiri dari minimal 6 karakter.", "error")
        elif password != konfirmasi:
            flash("Konfirmasi password tidak sama.", "error")

        else:
            try:
                conn.execute("""
                    INSERT INTO guru (
                        nama_lengkap, username, password
                    )
                    VALUES (?, ?, ?)
                """, (
                    nama,
                    username,
                    generate_password_hash(password)
                ))

                conn.commit()

                guru = conn.execute("""
                    SELECT *
                    FROM guru
                    WHERE username = ?
                """, (username,)).fetchone()

                session.clear()
                session["id_guru"] = guru["id_guru"]
                session["nama_guru"] = guru["nama_lengkap"]

                conn.close()

                return redirect(
                    url_for("dashboard")
                )

            except IntegrityError:
                flash("Nama pengguna sudah digunakan.", "error")

    conn.close()

    return render_template("auth/register.html", pesan=pesan)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/akun/pengaturan", methods=["GET", "POST"])
@login_required
def pengaturan_akun():
    conn = get_db_connection()

    guru = conn.execute("""
        SELECT *
        FROM guru
        WHERE id_guru = ?
    """, (session["id_guru"],)).fetchone()

    pesan = None

    if request.method == "POST":
        nama_baru = request.form.get(
            "nama_lengkap", ""
        ).strip()

        if not nama_baru:
            pesan = "Nama tidak boleh kosong."

        else:
            conn.execute("""
                UPDATE guru
                SET nama_lengkap = ?
                WHERE id_guru = ?
            """, (nama_baru, session["id_guru"]))

            conn.commit()

            session["nama_guru"] = nama_baru

            conn.close()

            flash("Nama guru berhasil diperbarui.", "success")
            return redirect(url_for("pengaturan_akun"))

    conn.close()

    return render_template(
        "akun/pengaturan.html",
        guru=guru,
        pesan=pesan
    )


@app.route("/akun/keamanan", methods=["GET", "POST"])
@login_required
def keamanan_akun():
    conn = get_db_connection()

    guru = conn.execute("""
        SELECT *
        FROM guru
        WHERE id_guru = ?
    """, (session["id_guru"],)).fetchone()

    pesan = None

    if request.method == "POST":
        username_baru = request.form.get(
            "username", ""
        ).strip()

        password_lama = request.form.get(
            "password_lama", ""
        )

        password_baru = request.form.get(
            "password_baru", ""
        )

        konfirmasi = request.form.get(
            "konfirmasi_password", ""
        )

        if not username_baru or not password_lama or not password_baru or not konfirmasi:
            pesan = "Semua data wajib diisi."

        elif not check_password_hash(
            guru["password"], password_lama
        ):
            pesan = "Password lama tidak sesuai."

        elif len(password_baru) < 6:
            pesan = "Password baru minimal 6 karakter."

        elif password_baru != konfirmasi:
            pesan = "Konfirmasi password tidak sama."

        else:
            try:
                conn.execute("""
                    UPDATE guru
                    SET username = ?, password = ?
                    WHERE id_guru = ?
                """, (
                    username_baru,
                    generate_password_hash(password_baru),
                    session["id_guru"]
                ))

                conn.commit()
                conn.close()

                flash(
                    "Username dan password berhasil diperbarui.",
                    "success"
                )

                return redirect(url_for("pengaturan_akun"))

            except IntegrityError:
                pesan = "Username sudah digunakan."

    conn.close()

    return render_template(
        "akun/keamanan.html",
        guru=guru,
        pesan=pesan
    )
# ==================================================
# PILIH MATA PELAJARAN
# ==================================================

@app.route("/mata-pelajaran")
@login_required
def halaman_pilih_mata_pelajaran():
    conn = get_db_connection()

    daftar_mata_pelajaran = conn.execute("""
        SELECT
            m.*,
            COUNT(
                CASE WHEN k.status_arsip = 0
                THEN k.id_kelas END
            ) AS jumlah_kelas
        FROM mata_pelajaran m
        LEFT JOIN kelas k
            ON m.id_mata_pelajaran = k.id_mata_pelajaran
        WHERE m.id_guru = ?
        AND m.status_arsip = 0
        GROUP BY m.id_mata_pelajaran
        ORDER BY m.nama_mata_pelajaran
    """, (session["id_guru"],)).fetchall()

    conn.close()

    return render_template(
        "mata_pelajaran/daftar.html",
        nama_guru=session["nama_guru"],
        daftar_mata_pelajaran=daftar_mata_pelajaran,
        id_mata_pelajaran_aktif=session.get("id_mata_pelajaran")
    )

@app.route("/mata-pelajaran/tambah", methods=["GET", "POST"])
@login_required
def tambah_mata_pelajaran():
    pesan = None
    nama = ""

    if request.method == "POST":
        nama = request.form.get(
            "nama_mata_pelajaran", ""
        ).strip()

        if not nama:
            pesan = "Nama mata pelajaran wajib diisi."

        else:
            conn = get_db_connection()

            jumlah_sebelumnya = conn.execute("""
                SELECT COUNT(*)
                FROM mata_pelajaran
                WHERE id_guru = ?
            """, (session["id_guru"],)).fetchone()[0]

            try:
                cursor = conn.execute("""
                    INSERT INTO mata_pelajaran (
                        id_guru, nama_mata_pelajaran
                    )
                    VALUES (?, ?)
                    RETURNING id_mata_pelajaran
                """, (session["id_guru"], nama))

                id_mata_pelajaran = cursor.fetchone()["id_mata_pelajaran"]
                conn.commit()
                conn.close()

                if jumlah_sebelumnya == 0:
                    session["id_mata_pelajaran"] = id_mata_pelajaran
                    session.pop("id_kelas", None)
                    session.pop("id_periode", None)
                    return redirect(url_for("dashboard"))

                return redirect(
                    url_for("halaman_pilih_mata_pelajaran")
                )

            except IntegrityError:
                pesan = "Mata pelajaran tersebut sudah ada."
                conn.close()

    return render_template(
        "mata_pelajaran/tambah.html",
        pesan=pesan,
        nama_mata_pelajaran=nama
    )


@app.route("/mata-pelajaran/pilih/<int:id_mata_pelajaran>")
@login_required
def pilih_mata_pelajaran(id_mata_pelajaran):
    mata_pelajaran = ambil_mata_pelajaran(
        id_mata_pelajaran
    )

    if not mata_pelajaran:
        flash("Mata pelajaran tidak ditemukan.")
        return redirect(url_for("daftar_mata_pelajaran"))

    session["id_mata_pelajaran"] = id_mata_pelajaran

    conn = get_db_connection()
    conn.execute("""
        UPDATE guru
        SET id_mata_pelajaran_terakhir = ?
        WHERE id_guru = ?
    """, (id_mata_pelajaran, session["id_guru"]))
    conn.commit()
    conn.close()

    session.pop("id_kelas", None)
    session.pop("id_periode", None)

    flash("Mata pelajaran berhasil dipilih.")
    return redirect(url_for("dashboard"))


# ==================================================
# KELOLA MATA PELAJARAN
# ==================================================

@app.route("/mata-pelajaran/kelola")
@login_required
def daftar_mata_pelajaran():
    conn = get_db_connection()

    daftar_mata_pelajaran = conn.execute("""
        SELECT
            m.id_mata_pelajaran,
            m.nama_mata_pelajaran,
            m.status_arsip,
            CASE
                WHEN m.id_mata_pelajaran = ? THEN 1
                ELSE 0
            END AS sedang_aktif,
            COUNT(
                CASE WHEN k.status_arsip = 0
                THEN k.id_kelas END
            ) AS jumlah_kelas
        FROM mata_pelajaran m
        LEFT JOIN kelas k
            ON m.id_mata_pelajaran = k.id_mata_pelajaran
        WHERE m.id_guru = ?
        GROUP BY m.id_mata_pelajaran
        ORDER BY m.nama_mata_pelajaran
    """, (
        session.get("id_mata_pelajaran"),
        session["id_guru"]
    )).fetchall()

    conn.close()

    return render_template(
        "mata_pelajaran/kelola.html",
        daftar_mata_pelajaran=daftar_mata_pelajaran
    )


@app.route("/mata-pelajaran/arsip/<int:id_mata_pelajaran>", methods=["POST"])
@login_required
def arsip_mata_pelajaran(id_mata_pelajaran):
    conn = get_db_connection()

    conn.execute("""
        UPDATE mata_pelajaran
        SET status_arsip = 1
        WHERE id_mata_pelajaran = ?
          AND id_guru = ?
    """, (id_mata_pelajaran, session["id_guru"]))

    conn.commit()
    conn.close()

    if session.get("id_mata_pelajaran") == id_mata_pelajaran:
        session.pop("id_mata_pelajaran", None)
        session.pop("id_kelas", None)
        session.pop("id_periode", None)

    flash("Mata pelajaran berhasil diarsipkan.")
    return redirect(url_for("daftar_mata_pelajaran"))


@app.route("/mata-pelajaran/pulihkan/<int:id_mata_pelajaran>", methods=["POST"])
@login_required
def pulihkan_mata_pelajaran(id_mata_pelajaran):
    conn = get_db_connection()

    conn.execute("""
        UPDATE mata_pelajaran
        SET status_arsip = 0
        WHERE id_mata_pelajaran = ?
          AND id_guru = ?
    """, (id_mata_pelajaran, session["id_guru"]))

    conn.commit()
    conn.close()

    flash("Mata pelajaran berhasil dipulihkan.")
    return redirect(url_for("daftar_mata_pelajaran"))


@app.route("/mata-pelajaran/hapus/<int:id_mata_pelajaran>", methods=["POST"])
@login_required
def hapus_mata_pelajaran(id_mata_pelajaran):
    conn = get_db_connection()

    try:
        mata_pelajaran = conn.execute("""
            SELECT id_mata_pelajaran
            FROM mata_pelajaran
            WHERE id_mata_pelajaran = ?
              AND id_guru = ?
        """, (
            id_mata_pelajaran,
            session["id_guru"]
        )).fetchone()

        if not mata_pelajaran:
            flash("Mata pelajaran tidak ditemukan.")
            return redirect(url_for("daftar_mata_pelajaran"))

        conn.execute("""
            DELETE FROM mata_pelajaran
            WHERE id_mata_pelajaran = ?
              AND id_guru = ?
        """, (
            id_mata_pelajaran,
            session["id_guru"]
        ))

        conn.commit()

    except IntegrityError:
        conn.rollback()
        flash("Mata pelajaran gagal dihapus karena masih memiliki data terkait.")
        return redirect(url_for("daftar_mata_pelajaran"))

    finally:
        conn.close()

    if session.get("id_mata_pelajaran") == id_mata_pelajaran:
        session.pop("id_mata_pelajaran", None)
        session.pop("id_kelas", None)
        session.pop("id_periode", None)

    flash("Mata pelajaran dan data terkait berhasil dihapus.")

    return redirect(url_for("daftar_mata_pelajaran"))


@app.route("/mata-pelajaran/edit/<int:id_mata_pelajaran>", methods=["GET", "POST"])
@login_required
def edit_mata_pelajaran(id_mata_pelajaran):
    conn = get_db_connection()
    pesan = None

    try:
        mata_pelajaran = conn.execute("""
            SELECT *
            FROM mata_pelajaran
            WHERE id_mata_pelajaran = ?
              AND id_guru = ?
        """, (
            id_mata_pelajaran,
            session["id_guru"]
        )).fetchone()

        if not mata_pelajaran:
            flash("Mata pelajaran tidak ditemukan.")
            return redirect(url_for("daftar_mata_pelajaran"))

        if request.method == "POST":
            nama = request.form.get(
                "nama_mata_pelajaran", ""
            ).strip()

            if not nama:
                pesan = "Nama mata pelajaran wajib diisi."

            else:
                try:
                    conn.execute("""
                        UPDATE mata_pelajaran
                        SET nama_mata_pelajaran = ?
                        WHERE id_mata_pelajaran = ?
                          AND id_guru = ?
                    """, (
                        nama,
                        id_mata_pelajaran,
                        session["id_guru"]
                    ))

                    conn.commit()
                    flash("Mata pelajaran berhasil diperbarui.")
                    return redirect(
                        url_for("daftar_mata_pelajaran")
                    )

                except IntegrityError:
                    conn.rollback()
                    pesan = "Nama mata pelajaran tersebut sudah ada."

        return render_template(
            "mata_pelajaran/edit.html",
            mata_pelajaran=mata_pelajaran,
            pesan=pesan
        )

    finally:
        conn.close()
# ==================================================
# KELOLA KELAS
# ==================================================

@app.route("/kelas/kelola")
@login_required
def kelola_kelas():
    id_mata_pelajaran = session.get("id_mata_pelajaran")
    id_kelas_aktif = session.get("id_kelas")
    mata_pelajaran = ambil_mata_pelajaran(id_mata_pelajaran)

    if not mata_pelajaran:
        return redirect(url_for("halaman_pilih_mata_pelajaran"))

    conn = get_db_connection()

    daftar_kelas = conn.execute("""
        SELECT
            k.*,
            COUNT(DISTINCT p.id_periode) AS jumlah_periode,
            COALESCE((
                SELECT COUNT(DISTINCT ps.id_siswa)
                FROM periode_siswa ps
                JOIN periode_akademik pa ON ps.id_periode = pa.id_periode
                WHERE pa.id_kelas = k.id_kelas AND ps.status_aktif = 1
            ), 0) AS jumlah_siswa
        FROM kelas k
        LEFT JOIN periode_akademik p
            ON k.id_kelas = p.id_kelas
        WHERE k.id_mata_pelajaran = ?
          AND k.status_arsip = 0
        GROUP BY k.id_kelas
        ORDER BY k.nama_kelas
    """, (id_mata_pelajaran,)).fetchall()

    kelas_arsip = conn.execute("""
        SELECT *
        FROM kelas
        WHERE id_mata_pelajaran = ?
          AND status_arsip = 1
        ORDER BY nama_kelas
    """, (id_mata_pelajaran,)).fetchall()

    conn.close()

    return render_template(
        "kelas/kelola.html",
        mata_pelajaran_aktif=mata_pelajaran,
        daftar_kelas=daftar_kelas,
        kelas_arsip=kelas_arsip,
        id_kelas_aktif=id_kelas_aktif
    )


@app.route("/kelas/tambah", methods=["POST"])
@login_required
def tambah_kelas():
    id_mata_pelajaran = request.form.get(
        "id_mata_pelajaran", type=int
    )
    nama_kelas = request.form.get("nama_kelas", "").strip()

    mata_pelajaran = ambil_mata_pelajaran(id_mata_pelajaran)

    if not mata_pelajaran or not nama_kelas:
        flash("Nama kelas wajib diisi.")
        return redirect(url_for("kelola_kelas"))

    conn = get_db_connection()

    try:
        cursor = conn.execute("""
            INSERT INTO kelas (
                id_mata_pelajaran, nama_kelas
            )
            VALUES (?, ?)
            RETURNING id_kelas
        """, (id_mata_pelajaran, nama_kelas))
        id_kelas_baru = cursor.fetchone()["id_kelas"]

        # Buat periode akademik awal otomatis agar kelas baru langsung dapat digunakan
        periode_sebelumnya = conn.execute("""
            SELECT p.tahun_ajaran, p.semester
            FROM periode_akademik p
            JOIN kelas k ON p.id_kelas = k.id_kelas
            JOIN mata_pelajaran m ON k.id_mata_pelajaran = m.id_mata_pelajaran
            WHERE m.id_guru = ?
            ORDER BY p.id_periode DESC
            LIMIT 1
        """, (session["id_guru"],)).fetchone()

        tahun_ajaran = periode_sebelumnya["tahun_ajaran"] if periode_sebelumnya else "2026/2027"
        semester = periode_sebelumnya["semester"] if periode_sebelumnya else "Ganjil"

        cursor_p = conn.execute("""
            INSERT INTO periode_akademik (
                id_kelas,
                tahun_ajaran,
                semester,
                status_aktif,
                status_terkunci
            )
            VALUES (?, ?, ?, 1, 0)
            RETURNING id_periode
        """, (id_kelas_baru, tahun_ajaran, semester))
        id_periode_baru = cursor_p.fetchone()["id_periode"]

        # Inisialisasi pengaturan penilaian standar
        conn.execute("""
            INSERT INTO pengaturan_penilaian (
                id_periode,
                bobot_sudah_diatur,
                grade_sudah_diatur,
                bobot_tugas,
                bobot_uts,
                bobot_uas,
                bobot_kehadiran,
                metode_tugas,
                jumlah_tugas,
                batas_a,
                batas_b,
                batas_c,
                batas_d,
                batas_e,
                kkm
            )
            VALUES (?, 1, 1, 30, 25, 30, 15, 'satu_per_satu', 0, 85, 75, 65, 50, 0, 75)
        """, (id_periode_baru,))

        conn.commit()

        # Update session langsung ke kelas dan periode baru
        session["id_mata_pelajaran"] = id_mata_pelajaran
        session["id_kelas"] = id_kelas_baru
        session["id_periode"] = id_periode_baru

        flash(f"Kelas {nama_kelas} berhasil ditambahkan dan langsung aktif.")

    except IntegrityError:
        flash("Nama kelas tersebut sudah digunakan.")

    finally:
        conn.close()

    return redirect(url_for("kelola_kelas"))


@app.route("/kelas/edit/<int:id_kelas>", methods=["GET", "POST"])
@login_required
def edit_kelas(id_kelas):
    kelas = ambil_kelas(id_kelas)

    if not kelas:
        flash("Kelas tidak ditemukan.")
        return redirect(url_for("kelola_kelas"))

    pesan = None

    if request.method == "POST":
        nama_baru = request.form.get(
            "nama_kelas", ""
        ).strip()

        if not nama_baru:
            pesan = "Nama kelas tidak boleh kosong."

        else:
            conn = get_db_connection()

            try:
                conn.execute("""
                    UPDATE kelas
                    SET nama_kelas = ?
                    WHERE id_kelas = ?
                """, (nama_baru, id_kelas))

                conn.commit()
                flash("Nama kelas berhasil diperbarui.", "success")
                return redirect(url_for("kelola_kelas"))

            except IntegrityError:
                pesan = "Nama kelas tersebut sudah digunakan."

            finally:
                conn.close()

    return render_template(
        "kelas/edit.html",
        kelas=kelas,
        pesan=pesan
    )

@app.route("/kelas/arsip/<int:id_kelas>", methods=["POST"])
@login_required
def arsip_kelas(id_kelas):
    kelas = ambil_kelas(id_kelas)

    if not kelas:
        flash("Kelas tidak ditemukan.")
        return redirect(url_for("kelola_kelas"))

    conn = get_db_connection()

    conn.execute("""
        UPDATE kelas
        SET status_arsip = 1
        WHERE id_kelas = ?
    """, (id_kelas,))

    conn.commit()
    conn.close()

    if session.get("id_kelas") == id_kelas:
        session.pop("id_kelas", None)
        session.pop("id_periode", None)

    flash("Kelas berhasil diarsipkan. Riwayat akademik tetap tersimpan.")
    return redirect(url_for("kelola_kelas"))


@app.route("/kelas/hapus/<int:id_kelas>", methods=["POST"])
@login_required
def hapus_kelas(id_kelas):
    # Memastikan kelas milik guru yang sedang login.
    # Kelas aktif maupun arsip boleh dihapus.
    kelas = ambil_kelas(id_kelas, termasuk_arsip=True)

    if not kelas:
        flash("Kelas tidak ditemukan atau bukan milik Anda.")
        return redirect(url_for("kelola_kelas"))

    id_guru = session["id_guru"]
    nama_kelas = kelas["nama_kelas"]

    conn = get_db_connection()

    try:
        # Mengambil identitas siswa yang pernah terdaftar
        # pada seluruh periode kelas ini.
        daftar_siswa = conn.execute("""
            SELECT DISTINCT ps.id_siswa
            FROM periode_siswa ps
            JOIN periode_akademik p
                ON ps.id_periode = p.id_periode
            WHERE p.id_kelas = ?
        """, (id_kelas,)).fetchall()

        # Menghapus riwayat perubahan kelas dan periodenya.
        conn.execute("""
            DELETE FROM riwayat_perubahan
            WHERE id_periode IN (
                SELECT id_periode
                FROM periode_akademik
                WHERE id_kelas = ?
            )
        """, (id_kelas,))

        # Menghapus kelas.
        # Periode, tugas, nilai, dan data turunannya
        # akan ikut terhapus melalui ON DELETE CASCADE.
        conn.execute("""
            DELETE FROM kelas
            WHERE id_kelas = ?
        """, (id_kelas,))

        # Menghapus identitas siswa hanya jika sudah tidak
        # digunakan pada periode lain milik guru tersebut.
        for siswa in daftar_siswa:
            conn.execute("""
                DELETE FROM siswa
                WHERE id_siswa = ?
                  AND id_guru = ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM periode_siswa
                      WHERE periode_siswa.id_siswa = siswa.id_siswa
                  )
            """, (siswa["id_siswa"], id_guru))

        conn.commit()

    except Exception:
        conn.rollback()
        flash("Kelas gagal dihapus. Tidak ada perubahan yang disimpan.")
        return redirect(url_for("kelola_kelas"))

    finally:
        conn.close()

    # Membersihkan pilihan kelas dan periode jika sedang digunakan.
    if session.get("id_kelas") == id_kelas:
        session.pop("id_kelas", None)
        session.pop("id_periode", None)

    flash(f"Kelas {nama_kelas} berhasil dihapus secara permanen.")
    return redirect(url_for("kelola_kelas"))


@app.route("/kelas/pulihkan/<int:id_kelas>", methods=["POST"])
@login_required
def pulihkan_kelas(id_kelas):
    kelas = ambil_kelas(id_kelas, termasuk_arsip=True)

    if not kelas or kelas["status_arsip"] != 1:
        flash("Kelas arsip tidak ditemukan.")
        return redirect(url_for("kelola_kelas"))

    conn = get_db_connection()

    conn.execute("""
        UPDATE kelas
        SET status_arsip = 0
        WHERE id_kelas = ?
    """, (id_kelas,))

    conn.commit()
    conn.close()

    flash("Kelas berhasil dipulihkan.")
    return redirect(url_for("kelola_kelas"))


@app.route("/kelas/pilih/<int:id_kelas>")
@login_required
def pilih_kelas(id_kelas):
    kelas = ambil_kelas(id_kelas)

    if not kelas:
        return redirect(url_for("kelola_kelas"))

    session["id_mata_pelajaran"] = kelas["id_mata_pelajaran"]
    session["id_kelas"] = id_kelas
    session.pop("id_periode", None)

    flash("Kelas berhasil diganti!", "success")

    conn = get_db_connection()

    periode = conn.execute("""
        SELECT *
        FROM periode_akademik
        WHERE id_kelas = ?
        ORDER BY status_aktif DESC, id_periode DESC
        LIMIT 1
    """, (id_kelas,)).fetchone()

    conn.close()

    if periode:
        session["id_periode"] = periode["id_periode"]

    tujuan = request.args.get("tujuan")
    if tujuan == "siswa":
        return redirect(url_for("daftar_siswa"))

    return redirect(url_for("kelola_kelas"))


# ==================================================
# KELOLA PERIODE AKADEMIK
# ==================================================

@app.route("/kelas/<int:id_kelas>/periode")
@login_required
def kelola_periode(id_kelas):
    kelas = ambil_kelas(id_kelas)

    if not kelas:
        return redirect(url_for("kelola_kelas"))

    conn = get_db_connection()

    daftar_periode = conn.execute("""
        SELECT *
        FROM periode_akademik
        WHERE id_kelas = ?
        ORDER BY id_periode DESC
    """, (id_kelas,)).fetchall()

    conn.close()

    return render_template(
        "periode/kelola.html",
        kelas=kelas,
        daftar_periode=daftar_periode
    )


@app.route("/periode/tambah", methods=["POST"])
@login_required
def tambah_periode():
    id_kelas = request.form.get("id_kelas", type=int)
    tahun_ajaran = request.form.get(
        "tahun_ajaran", ""
    ).strip()
    semester = request.form.get("semester", "").strip()
    salin_siswa = request.form.get("salin_siswa") == "on"

    kelas = ambil_kelas(id_kelas)

    if (
        not kelas
        or not tahun_ajaran
        or semester not in ("Ganjil", "Genap")
    ):
        flash("Data periode akademik belum lengkap.")
        return redirect(url_for("kelola_kelas"))

    conn = get_db_connection()

    try:
        periode_sebelumnya = conn.execute("""
            SELECT id_periode
            FROM periode_akademik
            WHERE id_kelas = ?
              AND status_aktif = 1
        """, (id_kelas,)).fetchone()

        # Periksa apakah periode sudah pernah dibuat
        sudah_ada = conn.execute("""
            SELECT id_periode
            FROM periode_akademik
            WHERE id_kelas = ?
              AND tahun_ajaran = ?
              AND semester = ?
        """, (id_kelas, tahun_ajaran, semester)).fetchone()

        if sudah_ada:
            flash("Periode akademik tersebut sudah ada.")
            return redirect(
                url_for("kelola_periode", id_kelas=id_kelas)
            )

        # Kunci periode aktif sebelumnya
        if periode_sebelumnya:
            conn.execute("""
                UPDATE periode_akademik
                SET status_aktif = 0,
                    status_terkunci = 1
                WHERE id_periode = ?
            """, (periode_sebelumnya["id_periode"],))

        # Buat periode baru sebagai periode aktif
        cursor = conn.execute("""
            INSERT INTO periode_akademik (
                id_kelas,
                tahun_ajaran,
                semester,
                status_aktif,
                status_terkunci
            )
            VALUES (?, ?, ?, 1, 0)
            RETURNING id_periode
        """, (id_kelas, tahun_ajaran, semester))

        id_periode_baru = cursor.fetchone()["id_periode"]

        # Pengaturan awal periode baru
        conn.execute("""
            INSERT INTO pengaturan_penilaian (id_periode)
            VALUES (?)
        """, (id_periode_baru,))

        # Salin daftar siswa saja, tanpa nilai dan tugas
        if salin_siswa and periode_sebelumnya:
            conn.execute("""
                INSERT INTO periode_siswa (
                    id_periode,
                    id_siswa,
                    no_absen,
                    status_aktif
                )
                SELECT
                    ?,
                    id_siswa,
                    no_absen,
                    status_aktif
                FROM periode_siswa
                WHERE id_periode = ?
            """, (
                id_periode_baru,
                periode_sebelumnya["id_periode"]
            ))

        conn.commit()

        session["id_kelas"] = id_kelas
        session["id_periode"] = id_periode_baru

        flash("Periode akademik baru berhasil dibuat.")

    except IntegrityError:
        conn.rollback()
        flash("Periode gagal dibuat. Periksa kembali data periode.")

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    return redirect(
        url_for("kelola_periode", id_kelas=id_kelas)
    )


@app.route("/periode/hapus/<int:id_periode>", methods=["POST"])
@login_required
def hapus_periode(id_periode):
    periode = ambil_periode(id_periode)

    if not periode:
        flash("Periode tidak ditemukan.")
        return redirect(url_for("dashboard"))

    id_kelas = periode["id_kelas"]

    kelas = ambil_kelas(id_kelas, termasuk_arsip=True)

    if not kelas:
        flash("Kelas tidak ditemukan.")
        return redirect(url_for("dashboard"))

    # Periode aktif atau belum terkunci tidak boleh dihapus.
    if periode["status_aktif"] == 1 or periode["status_terkunci"] != 1:
        flash("Periode aktif atau belum terkunci tidak dapat dihapus.")
        return redirect(
            url_for("kelola_periode", id_kelas=id_kelas)
        )

    conn = get_db_connection()

    try:
        # Hapus riwayat perubahan milik periode ini.
        conn.execute("""
            DELETE FROM riwayat_perubahan
            WHERE id_periode = ?
        """, (id_periode,))

        # Hapus periode.
        # Data turunannya ikut terhapus melalui ON DELETE CASCADE.
        conn.execute("""
            DELETE FROM periode_akademik
            WHERE id_periode = ?
              AND id_kelas = ?
              AND status_aktif = 0
              AND status_terkunci = 1
        """, (id_periode, id_kelas))

        conn.commit()

    except Exception:
        conn.rollback()
        flash("Periode gagal dihapus. Tidak ada perubahan yang disimpan.")
        return redirect(
            url_for("kelola_periode", id_kelas=id_kelas)
        )

    finally:
        conn.close()

    # Jika periode yang dihapus sedang dilihat,
    # arahkan pilihan kembali ke periode aktif.
    if session.get("id_periode") == id_periode:
        conn = get_db_connection()

        periode_aktif = conn.execute("""
            SELECT id_periode
            FROM periode_akademik
            WHERE id_kelas = ?
              AND status_aktif = 1
        """, (id_kelas,)).fetchone()

        conn.close()

        if periode_aktif:
            session["id_periode"] = periode_aktif["id_periode"]
            session["id_kelas"] = id_kelas
        else:
            session.pop("id_periode", None)

    flash("Periode akademik berhasil dihapus permanen.")

    return redirect(
        url_for("kelola_periode", id_kelas=id_kelas)
    )


@app.route("/periode/pilih/<int:id_periode>")
@login_required
def pilih_periode(id_periode):
    periode = ambil_periode(id_periode)

    if not periode:
        return redirect(url_for("dashboard"))

    kelas = ambil_kelas(periode["id_kelas"])

    if not kelas:
        return redirect(url_for("kelola_kelas"))

    # Hanya mengganti periode yang sedang dilihat.
    # Status aktif dan status terkunci tidak diubah.
    session["id_mata_pelajaran"] = kelas["id_mata_pelajaran"]
    session["id_kelas"] = periode["id_kelas"]
    session["id_periode"] = id_periode

    return redirect(url_for("dashboard"))


@app.route("/periode/aktifkan/<int:id_periode>", methods=["POST"])
@login_required
def aktifkan_periode(id_periode):
    periode = ambil_periode(id_periode)

    if not periode:
        flash("Periode tidak ditemukan.")
        return redirect(url_for("dashboard"))

    id_kelas = periode["id_kelas"]

    kelas = ambil_kelas(id_kelas, termasuk_arsip=True)

    if not kelas:
        flash("Kelas tidak ditemukan.")
        return redirect(url_for("dashboard"))

    conn = get_db_connection()

    try:
        # Kunci periode yang sedang aktif di kelas ini.
        conn.execute("""
            UPDATE periode_akademik
            SET status_aktif = 0,
                status_terkunci = 1
            WHERE id_kelas = ?
              AND status_aktif = 1
        """, (id_kelas,))

        # Aktifkan kembali periode yang dipilih.
        conn.execute("""
            UPDATE periode_akademik
            SET status_aktif = 1,
                status_terkunci = 0
            WHERE id_periode = ?
              AND id_kelas = ?
        """, (id_periode, id_kelas))

        conn.commit()

        session["id_kelas"] = id_kelas
        session["id_periode"] = id_periode
        session["id_mata_pelajaran"] = kelas["id_mata_pelajaran"]

        flash("Periode berhasil diaktifkan kembali.")

    except Exception:
        conn.rollback()
        flash("Periode gagal diaktifkan.")

    finally:
        conn.close()

    return redirect(
        url_for("kelola_periode", id_kelas=id_kelas)
    )


# ==================================================
# KELOLA DATA SISWA
# ==================================================

@app.route("/siswa")
@login_required
def daftar_siswa():
    id_periode = session.get("id_periode")
    sumber = request.args.get("sumber", "dashboard")

    if sumber not in ["dashboard", "data_siswa", "penilaian"]:
        sumber = "dashboard"

    periode = ambil_periode(id_periode)
    if not periode:
        periode = pastikan_periode_aktif()
        if periode:
            id_periode = periode["id_periode"]

    if not periode:
        flash("Pilih atau buat kelas dan periode akademik terlebih dahulu.")
        return redirect(url_for("kelola_kelas"))

    kelas = ambil_kelas(periode["id_kelas"])

    if not kelas:
        return redirect(url_for("kelola_kelas"))

    conn = get_db_connection()

    # Siswa yang masih aktif.
    daftar_siswa = conn.execute("""
        SELECT
            s.id_siswa,
            s.nama_siswa,
            ps.no_absen,
            ps.status_aktif
        FROM periode_siswa ps
        JOIN siswa s ON ps.id_siswa = s.id_siswa
        WHERE ps.id_periode = ?
          AND ps.status_aktif = 1
        ORDER BY ps.no_absen
    """, (id_periode,)).fetchall()

    # Siswa yang sudah diarsipkan.
    siswa_arsip = conn.execute("""
        SELECT
            s.id_siswa,
            s.nama_siswa,
            ps.no_absen,
            ps.status_aktif
        FROM periode_siswa ps
        JOIN siswa s ON ps.id_siswa = s.id_siswa
        WHERE ps.id_periode = ?
          AND ps.status_aktif = 0
        ORDER BY ps.no_absen
    """, (id_periode,)).fetchall()

    pengaturan = conn.execute("""
        SELECT bobot_sudah_diatur
        FROM pengaturan_penilaian
        WHERE id_periode = ?
    """, (id_periode,)).fetchone()

    conn.close()

    return render_template(
        "siswa/daftar.html",
        kelas=kelas,
        periode=periode,
        daftar_siswa=daftar_siswa,
        siswa_arsip=siswa_arsip,
        pengaturan=pengaturan
    )


@app.route("/siswa/tambah", methods=["POST"])
@login_required
def tambah_siswa():
    id_periode = session.get("id_periode")

    periode = ambil_periode(id_periode)
    if not periode:
        periode = pastikan_periode_aktif()
        if periode:
            id_periode = periode["id_periode"]

    if not periode:
        flash("Pilih atau buat kelas dan periode akademik terlebih dahulu.")
        return redirect(url_for("kelola_kelas"))

    kelas = ambil_kelas(periode["id_kelas"])

    if not kelas:
        return redirect(url_for("kelola_kelas"))

    if periode["status_terkunci"] == 1:
        flash("Periode ini sudah terkunci. Data siswa tidak dapat diubah.")
        return redirect(url_for("daftar_siswa"))

    nama = request.form.get("nama_siswa", "").strip()
    no_absen = request.form.get("no_absen", type=int)

    if not nama or no_absen is None:
        flash("Nama dan nomor absen wajib diisi.")
        return redirect(url_for("daftar_siswa"))

    if no_absen < 1:
        flash("Nomor absen harus lebih dari 0.")
        return redirect(url_for("daftar_siswa"))

    conn = get_db_connection()

    try:
        # Ambil siswa aktif berdasarkan urutan nomor absen.
        daftar_aktif = conn.execute("""
            SELECT id_periode_siswa
            FROM periode_siswa
            WHERE id_periode = ?
              AND status_aktif = 1
            ORDER BY no_absen
        """, (id_periode,)).fetchall()

        jumlah_siswa = len(daftar_aktif)

        # Nomor maksimal adalah jumlah siswa + 1.
        if no_absen > jumlah_siswa + 1:
            flash(
                f"Nomor absen maksimal {jumlah_siswa + 1}."
            )
            return redirect(url_for("daftar_siswa"))

        # Simpan nomor sementara untuk menghindari
        # bentrok dengan nomor absen yang sudah digunakan.
        for siswa in daftar_aktif:
            id_roster = siswa["id_periode_siswa"]

            conn.execute("""
                UPDATE periode_siswa
                SET no_absen = ?
                WHERE id_periode_siswa = ?
            """, (
                -1000000000 - id_roster,
                id_roster
            ))

        # Tambahkan identitas siswa.
        cursor = conn.execute("""
            INSERT INTO siswa (
                id_guru,
                nama_siswa
            )
            VALUES (?, ?)
            RETURNING id_siswa
        """, (
            session["id_guru"],
            nama
        ))

        id_siswa = cursor.fetchone()["id_siswa"]

        # Tambahkan siswa ke periode dengan nomor sementara.
        cursor = conn.execute("""
            INSERT INTO periode_siswa (
                id_periode,
                id_siswa,
                no_absen,
                status_aktif
            )
            VALUES (?, ?, ?, 1)
            RETURNING id_periode_siswa
        """, (
            id_periode,
            id_siswa,
            -2000000000 - id_siswa
        ))

        id_roster_baru = cursor.fetchone()["id_periode_siswa"]

        # Susun ulang posisi siswa.
        urutan = [
            siswa["id_periode_siswa"]
            for siswa in daftar_aktif
        ]

        urutan.insert(no_absen - 1, id_roster_baru)

        # Tetapkan nomor absen baru secara berurutan.
        for nomor, id_roster in enumerate(urutan, start=1):
            conn.execute("""
                UPDATE periode_siswa
                SET no_absen = ?
                WHERE id_periode_siswa = ?
                  AND id_periode = ?
            """, (
                nomor,
                id_roster,
                id_periode
            ))

        conn.commit()
        flash("Siswa berhasil ditambahkan dan nomor absen diperbarui.")

    except Exception:
        conn.rollback()
        flash("Siswa gagal ditambahkan. Silakan coba kembali.")

    finally:
        conn.close()

    return redirect(url_for("daftar_siswa"))


@app.route("/siswa/edit/<int:id_siswa>", methods=["GET", "POST"])
@login_required
def edit_siswa(id_siswa):
    id_periode = session.get("id_periode")

    periode = ambil_periode(id_periode)

    if not periode:
        return redirect(url_for("dashboard"))

    kelas = ambil_kelas(periode["id_kelas"])

    if not kelas:
        return redirect(url_for("kelola_kelas"))

    conn = get_db_connection()

    siswa = conn.execute("""
        SELECT
            s.id_siswa,
            s.nama_siswa,
            ps.no_absen,
            ps.status_aktif
        FROM siswa s
        JOIN periode_siswa ps
            ON s.id_siswa = ps.id_siswa
        WHERE s.id_siswa = ?
          AND ps.id_periode = ?
          AND s.id_guru = ?
    """, (
        id_siswa,
        id_periode,
        session["id_guru"]
    )).fetchone()

    if not siswa:
        conn.close()
        flash("Data siswa tidak ditemukan.")
        return redirect(url_for("daftar_siswa"))

    if siswa["status_aktif"] != 1:
        conn.close()
        flash("Siswa yang diarsipkan tidak dapat diedit.")
        return redirect(url_for("daftar_siswa"))

    if periode["status_terkunci"] == 1:
        conn.close()
        flash("Periode ini sudah terkunci.")
        return redirect(url_for("daftar_siswa"))

    if request.method == "POST":
        nama = request.form.get("nama_siswa", "").strip()
        no_absen = request.form.get("no_absen", type=int)

        if not nama or no_absen is None:
            conn.close()
            flash("Nama dan nomor absen wajib diisi.")
            return redirect(url_for("edit_siswa", id_siswa=id_siswa))

        if no_absen < 1:
            conn.close()
            flash("Nomor absen harus lebih dari 0.")
            return redirect(url_for("edit_siswa", id_siswa=id_siswa))

        try:
            # Ambil seluruh siswa aktif pada periode ini.
            daftar_aktif = conn.execute("""
                SELECT id_periode_siswa, id_siswa
                FROM periode_siswa
                WHERE id_periode = ?
                  AND status_aktif = 1
                ORDER BY no_absen
            """, (id_periode,)).fetchall()

            jumlah_siswa = len(daftar_aktif)

            if no_absen > jumlah_siswa:
                flash(
                    f"Nomor absen maksimal {jumlah_siswa}."
                )
                return redirect(
                    url_for("edit_siswa", id_siswa=id_siswa)
                )

            # Susun ulang urutan siswa.
            urutan = [
                row["id_periode_siswa"]
                for row in daftar_aktif
                if row["id_siswa"] != id_siswa
            ]

            id_periode_siswa = next(
                row["id_periode_siswa"]
                for row in daftar_aktif
                if row["id_siswa"] == id_siswa
            )

            urutan.insert(no_absen - 1, id_periode_siswa)

            # Gunakan nomor sementara agar tidak bentrok
            # dengan indeks nomor absen unik.
            conn.execute("""
                UPDATE periode_siswa
                SET no_absen = -id_periode_siswa
                WHERE id_periode = ?
                  AND status_aktif = 1
            """, (id_periode,))

            # Simpan nomor absen baru secara berurutan.
            for nomor, id_roster in enumerate(urutan, start=1):
                conn.execute("""
                    UPDATE periode_siswa
                    SET no_absen = ?
                    WHERE id_periode_siswa = ?
                      AND id_periode = ?
                """, (nomor, id_roster, id_periode))

            # Perbarui nama siswa.
            conn.execute("""
                UPDATE siswa
                SET nama_siswa = ?
                WHERE id_siswa = ?
                  AND id_guru = ?
            """, (
                nama,
                id_siswa,
                session["id_guru"]
            ))

            conn.commit()
            flash("Data siswa dan nomor absen berhasil diperbarui.")

        except Exception:
            conn.rollback()
            flash("Data siswa gagal diperbarui.")

        finally:
            conn.close()

        return redirect(url_for("daftar_siswa"))

    conn.close()

    return render_template(
        "siswa/edit.html",
        siswa=siswa
    )


@app.route("/siswa/arsip/<int:id_siswa>", methods=["POST"])
@login_required
def arsipkan_siswa(id_siswa):
    id_periode = session.get("id_periode")

    periode = ambil_periode(id_periode)

    if not periode:
        return redirect(url_for("dashboard"))

    if periode["status_terkunci"] == 1:
        flash("Periode ini sudah terkunci.")
        return redirect(url_for("daftar_siswa"))

    conn = get_db_connection()

    try:
        siswa = conn.execute("""
            SELECT ps.id_periode_siswa, ps.no_absen
            FROM periode_siswa ps
            JOIN siswa s ON ps.id_siswa = s.id_siswa
            WHERE ps.id_siswa = ?
              AND ps.id_periode = ?
              AND ps.status_aktif = 1
              AND s.id_guru = ?
        """, (
            id_siswa,
            id_periode,
            session["id_guru"]
        )).fetchone()

        if not siswa:
            flash("Siswa tidak ditemukan atau sudah diarsipkan.")
            return redirect(url_for("daftar_siswa"))

        # Arsipkan siswa tanpa menghapus riwayat nilainya.
        conn.execute("""
            UPDATE periode_siswa
            SET status_aktif = 0
            WHERE id_periode_siswa = ?
        """, (siswa["id_periode_siswa"],))

        # Ambil siswa aktif yang tersisa.
        daftar_aktif = conn.execute("""
            SELECT id_periode_siswa
            FROM periode_siswa
            WHERE id_periode = ?
              AND status_aktif = 1
            ORDER BY no_absen
        """, (id_periode,)).fetchall()

        # Kosongkan sementara nomor absen aktif.
        for row in daftar_aktif:
            conn.execute("""
                UPDATE periode_siswa
                SET no_absen = ?
                WHERE id_periode_siswa = ?
            """, (
                -1000000000 - row["id_periode_siswa"],
                row["id_periode_siswa"]
            ))

        # Rapikan kembali nomor absen.
        for nomor, row in enumerate(daftar_aktif, start=1):
            conn.execute("""
                UPDATE periode_siswa
                SET no_absen = ?
                WHERE id_periode_siswa = ?
            """, (
                nomor,
                row["id_periode_siswa"]
            ))

        conn.commit()
        flash("Siswa berhasil diarsipkan.")

    except Exception:
        conn.rollback()
        flash("Siswa gagal diarsipkan.")

    finally:
        conn.close()

    return redirect(url_for("daftar_siswa"))


@app.route("/siswa/pulihkan/<int:id_siswa>", methods=["POST"])
@login_required
def pulihkan_siswa(id_siswa):
    id_periode = session.get("id_periode")
    periode = ambil_periode(id_periode)

    if not periode:
        return redirect(url_for("dashboard"))

    if periode["status_terkunci"] == 1:
        flash("Periode ini sudah terkunci.")
        return redirect(url_for("daftar_siswa"))

    conn = get_db_connection()

    try:
        siswa = conn.execute("""
            SELECT ps.id_periode_siswa, ps.no_absen
            FROM periode_siswa ps
            JOIN siswa s ON ps.id_siswa = s.id_siswa
            WHERE ps.id_siswa = ?
              AND ps.id_periode = ?
              AND ps.status_aktif = 0
              AND s.id_guru = ?
        """, (
            id_siswa, id_periode, session["id_guru"]
        )).fetchone()

        if not siswa:
            flash("Siswa tidak ditemukan di arsip.")
            return redirect(url_for("daftar_siswa"))

        daftar_aktif = conn.execute("""
            SELECT id_periode_siswa
            FROM periode_siswa
            WHERE id_periode = ?
              AND status_aktif = 1
            ORDER BY no_absen
        """, (id_periode,)).fetchall()

        posisi = max(1, min(
            siswa["no_absen"],
            len(daftar_aktif) + 1
        ))

        # Pindahkan nomor aktif ke nomor sementara.
        for row in daftar_aktif:
            conn.execute("""
                UPDATE periode_siswa
                SET no_absen = ?
                WHERE id_periode_siswa = ?
            """, (
                -1000000000 - row["id_periode_siswa"],
                row["id_periode_siswa"]
            ))

        id_roster = siswa["id_periode_siswa"]

        conn.execute("""
            UPDATE periode_siswa
            SET status_aktif = 1, no_absen = ?
            WHERE id_periode_siswa = ?
        """, (-2000000000 - id_roster, id_roster))

        urutan = [
            row["id_periode_siswa"]
            for row in daftar_aktif
        ]

        urutan.insert(posisi - 1, id_roster)

        for nomor, id_row in enumerate(urutan, start=1):
            conn.execute("""
                UPDATE periode_siswa
                SET no_absen = ?
                WHERE id_periode_siswa = ?
            """, (nomor, id_row))

        conn.commit()
        flash("Siswa berhasil dipulihkan.")

    except Exception:
        conn.rollback()
        flash("Siswa gagal dipulihkan.")

    finally:
        conn.close()

    return redirect(url_for("daftar_siswa"))


@app.route("/siswa/hapus/<int:id_siswa>", methods=["POST"])
@login_required
def hapus_siswa(id_siswa):
    id_periode = session.get("id_periode")
    periode = ambil_periode(id_periode)

    if not periode:
        return redirect(url_for("dashboard"))

    if periode["status_terkunci"] == 1:
        flash("Periode ini sudah terkunci.")
        return redirect(url_for("daftar_siswa"))

    conn = get_db_connection()

    try:
        siswa = conn.execute("""
            SELECT id_siswa
            FROM siswa
            WHERE id_siswa = ?
              AND id_guru = ?
        """, (id_siswa, session["id_guru"])).fetchone()

        if not siswa:
            flash("Data siswa tidak ditemukan.")
            return redirect(url_for("daftar_siswa"))

        roster = conn.execute("""
            SELECT id_periode_siswa
            FROM periode_siswa
            WHERE id_siswa = ?
        """, (id_siswa,)).fetchall()

        daftar_roster = [
            row["id_periode_siswa"] for row in roster
        ]

        if daftar_roster:
            placeholders = ",".join("?" for _ in daftar_roster)

            conn.execute(f"""
                DELETE FROM riwayat_perubahan
                WHERE id_periode_siswa IN ({placeholders})
            """, daftar_roster)

        conn.execute("""
            DELETE FROM periode_siswa
            WHERE id_siswa = ?
        """, (id_siswa,))

        conn.execute("""
            DELETE FROM siswa
            WHERE id_siswa = ?
              AND id_guru = ?
        """, (id_siswa, session["id_guru"]))

        # Rapikan hanya periode yang sedang dibuka.
        aktif = conn.execute("""
            SELECT id_periode_siswa
            FROM periode_siswa
            WHERE id_periode = ?
              AND status_aktif = 1
            ORDER BY no_absen
        """, (id_periode,)).fetchall()

        for row in aktif:
            conn.execute("""
                UPDATE periode_siswa
                SET no_absen = ?
                WHERE id_periode_siswa = ?
            """, (
                -1000000000 - row["id_periode_siswa"],
                row["id_periode_siswa"]
            ))

        for nomor, row in enumerate(aktif, start=1):
            conn.execute("""
                UPDATE periode_siswa
                SET no_absen = ?
                WHERE id_periode_siswa = ?
            """, (
                nomor,
                row["id_periode_siswa"]
            ))

        conn.commit()
        flash("Data siswa dan seluruh riwayatnya berhasil dihapus.")

    except Exception:
        conn.rollback()
        flash("Data siswa gagal dihapus.")

    finally:
        conn.close()

    return redirect(url_for("daftar_siswa"))


# ==================================================
# PENGATURAN BOBOT PENILAIAN
# ==================================================

@app.route("/penilaian/pengaturan", methods=["GET", "POST"])
@login_required
def pengaturan_bobot():
    id_periode = session.get("id_periode")
    periode = ambil_periode(id_periode)

    # Menentukan asal halaman.
    sumber = request.args.get("sumber", "dashboard")
    if sumber not in ["dashboard", "data_siswa"]:
        sumber = "dashboard"

    if not periode:
        # Coba auto-resolusi periode aktif milik guru
        conn = get_db_connection()
        p_fallback = conn.execute("""
            SELECT p.*, k.id_mata_pelajaran
            FROM periode_akademik p
            JOIN kelas k ON p.id_kelas = k.id_kelas
            JOIN mata_pelajaran m ON k.id_mata_pelajaran = m.id_mata_pelajaran
            WHERE m.id_guru = ?
              AND k.status_arsip = 0
              AND m.status_arsip = 0
            ORDER BY p.status_aktif DESC, p.id_periode DESC
            LIMIT 1
        """, (session["id_guru"],)).fetchone()
        conn.close()

        if p_fallback:
            session["id_periode"] = p_fallback["id_periode"]
            session["id_kelas"] = p_fallback["id_kelas"]
            session["id_mata_pelajaran"] = p_fallback["id_mata_pelajaran"]
            periode = ambil_periode(p_fallback["id_periode"])
            id_periode = p_fallback["id_periode"]

    if not periode:
        return render_template(
            "penilaian/pengaturan_bobot.html",
            sumber=sumber,
            periode=None,
            pengaturan=None,
            pengaturan_pertama=True,
            belum_ada_periode=True
        )

    if request.method == "POST" and periode["status_terkunci"] == 1:
        flash("Periode ini sudah terkunci dan tidak dapat diubah.")
        return redirect(url_for("pengaturan_bobot", sumber=sumber))

    conn = get_db_connection()

    pengaturan = conn.execute("""
        SELECT *
        FROM pengaturan_penilaian
        WHERE id_periode = ?
    """, (id_periode,)).fetchone()

    if not pengaturan:
        conn.execute("""
            INSERT INTO pengaturan_penilaian (id_periode)
            VALUES (?)
        """, (id_periode,))
        conn.commit()

        pengaturan = conn.execute("""
            SELECT *
            FROM pengaturan_penilaian
            WHERE id_periode = ?
        """, (id_periode,)).fetchone()

    pengaturan_pertama = not pengaturan["bobot_sudah_diatur"]

    berhasil_simpan = False

    if request.method == "POST":
        try:
            bobot_tugas = float(request.form.get("bobot_tugas", ""))
            bobot_uts = float(request.form.get("bobot_uts", ""))
            bobot_uas = float(request.form.get("bobot_uas", ""))
            bobot_kehadiran = float(
                request.form.get("bobot_kehadiran", "")
            )

            daftar_bobot = [
                bobot_tugas,
                bobot_uts,
                bobot_uas,
                bobot_kehadiran
            ]

            batas_a = float(request.form.get("batas_a", ""))
            batas_b = float(request.form.get("batas_b", ""))
            batas_c = float(request.form.get("batas_c", ""))
            batas_d = float(request.form.get("batas_d", ""))
            batas_e = 0

            daftar_grade = [
                batas_a, batas_b, batas_c, batas_d, batas_e
            ]

            kkm = float(request.form.get("kkm", ""))

            if any(not 0 <= nilai <= 100 for nilai in daftar_bobot):
                flash("Setiap bobot harus antara 0 sampai 100.")

            elif abs(sum(daftar_bobot) - 100) > 0.0001:
                flash("Jumlah bobot harus tepat 100%.")

            elif any(not 0 <= nilai <= 100 for nilai in daftar_grade):
                flash("Batas grade harus antara 0 sampai 100.")

            elif not (
                batas_a > batas_b > batas_c > batas_d > batas_e
            ):
                flash(
                    "Urutan batas grade harus dari A tertinggi "
                    "sampai E terendah."
                )

            elif not 0 <= kkm <= 100:
                flash("KKM harus antara 0 sampai 100.")

            else:
                conn.execute("""
                    UPDATE pengaturan_penilaian
                    SET
                        bobot_tugas = ?,
                        bobot_uts = ?,
                        bobot_uas = ?,
                        bobot_kehadiran = ?,
                        bobot_sudah_diatur = 1,
                        batas_a = ?,
                        batas_b = ?,
                        batas_c = ?,
                        batas_d = ?,
                        batas_e = ?,
                        kkm = ?,
                        grade_sudah_diatur = 1
                    WHERE id_periode = ?
                """, (
                    bobot_tugas,
                    bobot_uts,
                    bobot_uas,
                    bobot_kehadiran,
                    batas_a,
                    batas_b,
                    batas_c,
                    batas_d,
                    batas_e,
                    kkm,
                    id_periode
                ))

                conn.commit()
                berhasil_simpan = True
                flash(
                    "Pengaturan bobot, grade, dan KKM berhasil disimpan."
                )

        except (ValueError, TypeError):
            flash("Pastikan semua kolom diisi dengan angka yang valid.")

        finally:
            conn.close()

        if berhasil_simpan:
            return redirect(
                url_for("pengaturan_bobot", sumber=sumber)
            )
        conn.close()

    return render_template(
        "penilaian/pengaturan_bobot.html",
        periode=periode,
        pengaturan=pengaturan,
        sumber=sumber
    )

# ==================================================
# API PENCARIAN GLOBAL
# ==================================================
@app.route("/api/search")
@login_required
def api_search():
    q = request.args.get("q", "").strip()
    conn = get_db_connection()
    id_guru = session.get("id_guru")

    hasil_siswa = []
    hasil_kelas = []
    hasil_mapel = []
    hasil_fitur = []

    fitur_list = [
        {"judul": "Data Siswa", "kategori": "Data", "deskripsi": "Kelola daftar nama dan data siswa", "url": url_for("daftar_siswa"), "tipe": "siswa"},
        {"judul": "Pengaturan Bobot Penilaian", "kategori": "Penilaian", "deskripsi": "Atur persentase bobot tugas, UTS, UAS", "url": url_for("pengaturan_bobot", sumber="dashboard"), "tipe": "bobot"},
        {"judul": "Perhitungan & Input Nilai", "kategori": "Penilaian", "deskripsi": "Input nilai harian, UTS, dan UAS", "url": url_for("halaman_penilaian"), "tipe": "nilai"},
        {"judul": "Rekapitulasi Nilai", "kategori": "Laporan", "deskripsi": "Lihat hasil nilai akhir dan cetak rapor", "url": url_for("rekapitulasi_nilai"), "tipe": "rekap"},
        {"judul": "Kelola Kelas", "kategori": "Akademik", "deskripsi": "Tambah, edit, dan kelola kelas akademik", "url": url_for("kelola_kelas"), "tipe": "kelas"},
        {"judul": "Kelola Mata Pelajaran", "kategori": "Akademik", "deskripsi": "Daftar mata pelajaran yang diampu", "url": url_for("daftar_mata_pelajaran"), "tipe": "mapel"},
        {"judul": "Ganti Mata Pelajaran", "kategori": "Akademik", "deskripsi": "Beralih ruang kerja mata pelajaran aktif", "url": url_for("halaman_pilih_mata_pelajaran"), "tipe": "mapel"},
        {"judul": "Pengaturan Akun", "kategori": "Profil", "deskripsi": "Kelola informasi profil guru", "url": url_for("pengaturan_akun"), "tipe": "profil"},
        {"judul": "Keamanan & Password", "kategori": "Profil", "deskripsi": "Ubah username dan password akun", "url": url_for("keamanan_akun"), "tipe": "keamanan"},
    ]

    if not q:
        hasil_fitur = fitur_list[:4]
        kelas_rows = conn.execute("""
            SELECT k.id_kelas, k.nama_kelas, m.nama_mata_pelajaran
            FROM kelas k
            JOIN mata_pelajaran m ON m.id_mata_pelajaran = k.id_mata_pelajaran
            WHERE m.id_guru = ? AND k.status_arsip = 0
            LIMIT 3
        """, (id_guru,)).fetchall()
        for r in kelas_rows:
            hasil_kelas.append({
                "id": r["id_kelas"],
                "judul": f"Kelas {r['nama_kelas']}",
                "kategori": "Kelas",
                "deskripsi": f"Mapel: {r['nama_mata_pelajaran']}",
                "url": url_for("kelola_kelas"),
                "tipe": "kelas"
            })
    else:
        q_lower = q.lower()
        keyword = f"%{q_lower}%"

        hasil_fitur = [
            f for f in fitur_list
            if q_lower in f["judul"].lower() or q_lower in f["deskripsi"].lower() or q_lower in f["kategori"].lower()
        ]

        # Siswa
        siswa_rows = conn.execute("""
            SELECT DISTINCT s.id_siswa, s.nama_siswa, s.nomor_identitas
            FROM siswa s
            WHERE s.id_guru = ? AND s.status_arsip = 0
              AND (LOWER(s.nama_siswa) LIKE ? OR LOWER(COALESCE(s.nomor_identitas, '')) LIKE ?)
            LIMIT 6
        """, (id_guru, keyword, keyword)).fetchall()

        for r in siswa_rows:
            nomor = f" • NIS: {r['nomor_identitas']}" if r["nomor_identitas"] else ""
            hasil_siswa.append({
                "id": r["id_siswa"],
                "judul": r["nama_siswa"],
                "kategori": "Siswa",
                "deskripsi": f"Data Siswa{nomor}",
                "url": url_for("daftar_siswa"),
                "tipe": "siswa"
            })

        # Kelas
        kelas_rows = conn.execute("""
            SELECT k.id_kelas, k.nama_kelas, m.nama_mata_pelajaran
            FROM kelas k
            JOIN mata_pelajaran m ON m.id_mata_pelajaran = k.id_mata_pelajaran
            WHERE m.id_guru = ? AND k.status_arsip = 0
              AND (LOWER(k.nama_kelas) LIKE ? OR LOWER(m.nama_mata_pelajaran) LIKE ?)
            LIMIT 4
        """, (id_guru, keyword, keyword)).fetchall()

        for r in kelas_rows:
            hasil_kelas.append({
                "id": r["id_kelas"],
                "judul": f"Kelas {r['nama_kelas']}",
                "kategori": "Kelas",
                "deskripsi": f"Mata Pelajaran: {r['nama_mata_pelajaran']}",
                "url": url_for("kelola_kelas"),
                "tipe": "kelas"
            })

        # Mata Pelajaran
        mapel_rows = conn.execute("""
            SELECT id_mata_pelajaran, nama_mata_pelajaran
            FROM mata_pelajaran
            WHERE id_guru = ? AND status_arsip = 0
              AND LOWER(nama_mata_pelajaran) LIKE ?
            LIMIT 4
        """, (id_guru, keyword)).fetchall()

        for r in mapel_rows:
            hasil_mapel.append({
                "id": r["id_mata_pelajaran"],
                "judul": f"Mata Pelajaran {r['nama_mata_pelajaran']}",
                "kategori": "Mata Pelajaran",
                "deskripsi": "Klik untuk beralih mata pelajaran aktif",
                "url": url_for("pilih_mata_pelajaran", id_mata_pelajaran=r["id_mata_pelajaran"]),
                "tipe": "mapel"
            })

    conn.close()

    total = len(hasil_siswa) + len(hasil_kelas) + len(hasil_mapel) + len(hasil_fitur)

    return jsonify({
        "status": "success",
        "query": q,
        "total": total,
        "results": {
            "fitur": hasil_fitur,
            "siswa": hasil_siswa,
            "kelas": hasil_kelas,
            "mapel": hasil_mapel
        }
    })


# ==================================================
# DASHBOARD
# ==================================================
@app.route("/dashboard")
@login_required
def dashboard():
    id_mata_pelajaran = session.get("id_mata_pelajaran")

    conn = get_db_connection()
    hari_ini = date.today()
    nama_bulan = [
        "Januari", "Februari", "Maret", "April", "Mei", "Juni",
        "Juli", "Agustus", "September", "Oktober", "November", "Desember"
    ]
    nama_hari = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
    tanggal_hari_ini = (
        f"{nama_hari[hari_ini.weekday()]}, {hari_ini.day} "
        f"{nama_bulan[hari_ini.month - 1]} {hari_ini.year}"
    )

    # Mengambil semua mata pelajaran milik guru
    daftar_mata_pelajaran = conn.execute("""
        SELECT
            m.id_mata_pelajaran,
            m.nama_mata_pelajaran,
            COUNT(
                CASE
                    WHEN k.status_arsip = 0
                    THEN k.id_kelas
                END
            ) AS jumlah_kelas
        FROM mata_pelajaran m
        LEFT JOIN kelas k
            ON k.id_mata_pelajaran = m.id_mata_pelajaran
        WHERE m.id_guru = ?
          AND m.status_arsip = 0
        GROUP BY m.id_mata_pelajaran
        ORDER BY m.nama_mata_pelajaran
    """, (session["id_guru"],)).fetchall()
    # Jika belum ada mata pelajaran yang dipilih,
    # pilih mata pelajaran pertama milik guru.
    if not id_mata_pelajaran and daftar_mata_pelajaran:
        id_mata_pelajaran = daftar_mata_pelajaran[0]["id_mata_pelajaran"]
        session["id_mata_pelajaran"] = id_mata_pelajaran

    mata_pelajaran = ambil_mata_pelajaran(id_mata_pelajaran)

    # Jika belum ada mata pelajaran yang sedang dipilih
    if not mata_pelajaran:
        session.pop("id_mata_pelajaran", None)
        session.pop("id_kelas", None)
        session.pop("id_periode", None)

        conn.close()

        return render_template(
            "dashboard/dashboard.html",
            nama_guru=session["nama_guru"],
            mata_pelajaran_aktif=None,
            daftar_mata_pelajaran=daftar_mata_pelajaran,
            daftar_kelas=[],
            kelas_aktif=None,
            daftar_periode=[],
            periode_aktif=None,
            periode_resmi_aktif=None,
            total_siswa=0,
            tanggal_hari_ini=tanggal_hari_ini
        )

    daftar_kelas = conn.execute("""
        SELECT
            k.*,
            p.tahun_ajaran,
            p.semester,
            (
                SELECT COUNT(*)
                FROM periode_siswa ps
                WHERE ps.id_periode = p.id_periode
                  AND ps.status_aktif = 1
            ) AS jumlah_siswa
        FROM kelas k
        LEFT JOIN periode_akademik p
            ON p.id_kelas = k.id_kelas
           AND p.status_aktif = 1
        WHERE k.id_mata_pelajaran = ?
          AND k.status_arsip = 0
        ORDER BY k.nama_kelas
    """, (id_mata_pelajaran,)).fetchall()

    kelas_aktif = None
    daftar_periode = []
    periode_aktif = None
    periode_resmi_aktif = None

    id_kelas = session.get("id_kelas")

    if id_kelas:
        kelas_aktif = conn.execute("""
            SELECT *
            FROM kelas
            WHERE id_kelas = ?
              AND id_mata_pelajaran = ?
              AND status_arsip = 0
        """, (id_kelas, id_mata_pelajaran)).fetchone()

    if not kelas_aktif:
        session.pop("id_kelas", None)
        session.pop("id_periode", None)

        if daftar_kelas:
            kelas_aktif = daftar_kelas[0]
            session["id_kelas"] = kelas_aktif["id_kelas"]

    if kelas_aktif:
        id_kelas_aktif = kelas_aktif["id_kelas"]

        daftar_periode = conn.execute("""
            SELECT *
            FROM periode_akademik
            WHERE id_kelas = ?
            ORDER BY id_periode DESC
        """, (id_kelas_aktif,)).fetchall()

        periode_resmi_aktif = conn.execute("""
            SELECT *
            FROM periode_akademik
            WHERE id_kelas = ?
              AND status_aktif = 1
            LIMIT 1
        """, (id_kelas_aktif,)).fetchone()

        id_periode = session.get("id_periode")

        if id_periode:
            periode_aktif = conn.execute("""
                SELECT *
                FROM periode_akademik
                WHERE id_periode = ?
                  AND id_kelas = ?
            """, (id_periode, id_kelas_aktif)).fetchone()

        if not periode_aktif:
            periode_aktif = periode_resmi_aktif

            if not periode_aktif and daftar_periode:
                periode_aktif = daftar_periode[0]

            if periode_aktif:
                session["id_periode"] = periode_aktif["id_periode"]
        total_siswa = 0
        if periode_aktif:
            total_siswa = conn.execute("""
                SELECT COUNT(*)
                FROM periode_siswa
                WHERE id_periode = ? AND status_aktif = 1
            """, (periode_aktif["id_periode"],)).fetchone()[0]
    else:
        total_siswa = 0

    conn.close()

    return render_template(
        "dashboard/dashboard.html",
        nama_guru=session["nama_guru"],
        mata_pelajaran_aktif=mata_pelajaran,
        daftar_mata_pelajaran=daftar_mata_pelajaran,
        daftar_kelas=daftar_kelas,
        kelas_aktif=kelas_aktif,
        daftar_periode=daftar_periode,
        periode_aktif=periode_aktif,
        periode_resmi_aktif=periode_resmi_aktif,
        total_siswa=total_siswa,
        tanggal_hari_ini=tanggal_hari_ini
    )
# ==================================================
# HALAMAN PENILAIAN SISWA
# ==================================================

@app.route("/penilaian", methods=["GET", "POST"])
@login_required
def halaman_penilaian():
    id_periode = session.get("id_periode")
    periode = ambil_periode(id_periode)
    if not periode:
        periode = pastikan_periode_aktif()
        if periode:
            id_periode = periode["id_periode"]

    if not periode:
        flash("Pilih atau buat kelas dan periode akademik terlebih dahulu.")
        return redirect(url_for("kelola_kelas"))

    conn = get_db_connection()

    try:
        pengaturan = conn.execute("""
            SELECT *
            FROM pengaturan_penilaian
            WHERE id_periode = ?
        """, (id_periode,)).fetchone()

        if not pengaturan or pengaturan["bobot_sudah_diatur"] != 1:
            flash(
                "Bobot penilaian belum diatur. "
                "Silakan atur melalui menu Pengaturan Bobot."
            )
            return redirect(url_for("dashboard"))

        if pengaturan["grade_sudah_diatur"] != 1:
            flash(
                "Batas Grade dan KKM belum diatur. "
                "Silakan lengkapi melalui menu Pengaturan Bobot."
            )
            return redirect(url_for("dashboard"))

        if request.method == "POST":
            if periode["status_terkunci"] == 1:
                flash("Periode ini sudah terkunci.")
                return redirect(url_for("halaman_penilaian"))

            aksi = request.form.get("aksi")

            # ------------------------------------------
            # SIMPAN METODE TUGAS
            # ------------------------------------------
            if aksi == "simpan_metode":
                metode = request.form.get("metode_tugas")

                if metode not in [
                    "satu_per_satu",
                    "langsung_rata_rata"
                ]:
                    flash("Metode pengisian tidak valid.")
                else:
                    conn.execute("""
                        UPDATE pengaturan_penilaian
                        SET metode_tugas = ?
                        WHERE id_periode = ?
                    """, (metode, id_periode))

                    conn.commit()
                    flash("Metode pengisian berhasil disimpan.")

            # ------------------------------------------
            # ATUR JUMLAH TUGAS
            # ------------------------------------------
            elif aksi == "atur_jumlah_tugas":
                if pengaturan["metode_tugas"] != "satu_per_satu":
                    flash("Pengaturan jumlah tugas hanya untuk metode satu per satu.")
                else:
                    try:
                        jumlah_baru = int(
                            request.form.get("jumlah_tugas", "")
                        )

                        jumlah_lama = conn.execute("""
                            SELECT COUNT(*)
                            FROM tugas
                            WHERE id_periode = ?
                        """, (id_periode,)).fetchone()[0]

                        if jumlah_baru < 1:
                            flash("Jumlah tugas minimal 1.")

                        elif jumlah_baru > 100:
                            flash("Jumlah tugas maksimal 100.")

                        elif jumlah_baru < jumlah_lama:
                            try:
                                berhasil_dikurangi = kurangi_jumlah_tugas(
                                    conn, id_periode, jumlah_lama, jumlah_baru
                                )
                            except Exception:
                                conn.rollback()
                                raise

                            if not berhasil_dikurangi:
                                flash(
                                    "Jumlah tugas tidak bisa dikurangi: nilai tersimpan pada tugas yang akan dihapus. "
                                    "Kosongkan nilai tersebut terlebih dahulu."
                                )
                            else:
                                flash(f"Jumlah tugas berhasil dikurangi menjadi {jumlah_baru}.")

                        else:
                            for nomor in range(
                                jumlah_lama + 1,
                                jumlah_baru + 1
                            ):
                                conn.execute("""
                                    INSERT INTO tugas (
                                        id_periode,
                                        nama_tugas
                                    )
                                    VALUES (?, ?)
                                """, (
                                    id_periode,
                                    f"Tugas {nomor}"
                                ))

                            conn.execute("""
                                UPDATE pengaturan_penilaian
                                SET jumlah_tugas = ?
                                WHERE id_periode = ?
                            """, (jumlah_baru, id_periode))

                            conn.commit()
                            flash(
                                f"Jumlah tugas berhasil diatur menjadi {jumlah_baru}."
                            )

                    except (ValueError, TypeError):
                        flash("Jumlah tugas harus berupa angka.")

            # ------------------------------------------
            # SIMPAN NILAI SISWA
            # ------------------------------------------
            elif aksi == "simpan_nilai":
                def baca_nilai(nama_field):
                    teks = request.form.get(nama_field, "").strip()

                    if teks == "":
                        return None

                    nilai = float(teks)

                    if not 0 <= nilai <= 100:
                        raise ValueError

                    return nilai

                try:
                    daftar_siswa_form = conn.execute("""
                        SELECT id_periode_siswa
                        FROM periode_siswa
                        WHERE id_periode = ?
                          AND status_aktif = 1
                    """, (id_periode,)).fetchall()

                    metode = pengaturan["metode_tugas"]

                    for siswa in daftar_siswa_form:
                        id_roster = siswa["id_periode_siswa"]

                        # Nilai tugas satu per satu
                        if metode == "satu_per_satu":
                            daftar_tugas = conn.execute("""
                                SELECT id_tugas
                                FROM tugas
                                WHERE id_periode = ?
                                ORDER BY id_tugas
                            """, (id_periode,)).fetchall()

                            for tugas in daftar_tugas:
                                id_tugas = tugas["id_tugas"]

                                nilai = baca_nilai(
                                    f"tugas_{id_roster}_{id_tugas}"
                                )

                                conn.execute("""
                                    INSERT INTO nilai_tugas (
                                        id_tugas,
                                        id_periode_siswa,
                                        nilai
                                    )
                                    VALUES (?, ?, ?)
                                    ON CONFLICT (
                                        id_tugas,
                                        id_periode_siswa
                                    )
                                    DO UPDATE SET
                                        nilai = excluded.nilai,
                                        diperbarui_pada = CURRENT_TIMESTAMP
                                """, (
                                    id_tugas,
                                    id_roster,
                                    nilai
                                ))

                        # Nilai tugas langsung rata-rata
                        else:
                            nilai_rata = baca_nilai(
                                f"rata_rata_{id_roster}"
                            )

                            conn.execute("""
                                INSERT INTO nilai_tugas_rata_rata (
                                    id_periode_siswa,
                                    nilai
                                )
                                VALUES (?, ?)
                                ON CONFLICT (id_periode_siswa)
                                DO UPDATE SET
                                    nilai = excluded.nilai,
                                    diperbarui_pada = CURRENT_TIMESTAMP
                            """, (id_roster, nilai_rata))

                        # Nilai UTS, UAS, dan kehadiran
                        nilai_uts = baca_nilai(f"uts_{id_roster}")
                        nilai_uas = baca_nilai(f"uas_{id_roster}")
                        nilai_kehadiran = baca_nilai(
                            f"kehadiran_{id_roster}"
                        )

                        conn.execute("""
                            INSERT INTO nilai_komponen (
                                id_periode_siswa,
                                nilai_uts,
                                nilai_uas,
                                nilai_kehadiran
                            )
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT (id_periode_siswa)
                            DO UPDATE SET
                                nilai_uts = excluded.nilai_uts,
                                nilai_uas = excluded.nilai_uas,
                                nilai_kehadiran = excluded.nilai_kehadiran,
                                diperbarui_pada = CURRENT_TIMESTAMP
                        """, (
                            id_roster,
                            nilai_uts,
                            nilai_uas,
                            nilai_kehadiran
                        ))

                    conn.commit()
                    flash("Nilai siswa berhasil disimpan.")

                except (ValueError, TypeError):
                    conn.rollback()
                    flash(
                        "Nilai harus berupa angka antara 0 sampai 100."
                    )

            else:
                flash("Permintaan tidak dikenali.")

            return redirect(url_for("halaman_penilaian"))

        # ----------------------------------------------
        # AMBIL DAFTAR SISWA
        # ----------------------------------------------
        daftar_siswa = conn.execute("""
            SELECT
                ps.id_periode_siswa,
                ps.no_absen,
                s.nama_siswa
            FROM periode_siswa ps
            JOIN siswa s
                ON ps.id_siswa = s.id_siswa
            WHERE ps.id_periode = ?
              AND ps.status_aktif = 1
            ORDER BY ps.no_absen ASC
        """, (id_periode,)).fetchall()

        # ----------------------------------------------
        # AMBIL DAFTAR TUGAS
        # ----------------------------------------------
        daftar_tugas = conn.execute("""
            SELECT *
            FROM tugas
            WHERE id_periode = ?
            ORDER BY id_tugas ASC
        """, (id_periode,)).fetchall()

        # ----------------------------------------------
        # AMBIL NILAI TUGAS SATU PER SATU
        # ----------------------------------------------
        nilai_tugas = {}

        for row in conn.execute("""
            SELECT
                nt.id_periode_siswa,
                nt.id_tugas,
                nt.nilai
            FROM nilai_tugas nt
            JOIN tugas t ON nt.id_tugas = t.id_tugas
            WHERE t.id_periode = ?
        """, (id_periode,)).fetchall():

            nilai_tugas[
                (row["id_periode_siswa"], row["id_tugas"])
            ] = row["nilai"]

        # ----------------------------------------------
        # AMBIL NILAI TUGAS RATA-RATA
        # ----------------------------------------------
        nilai_rata_rata = {}

        for row in conn.execute("""
            SELECT
                ntr.id_periode_siswa,
                ntr.nilai
            FROM nilai_tugas_rata_rata ntr
            JOIN periode_siswa ps
                ON ntr.id_periode_siswa = ps.id_periode_siswa
            WHERE ps.id_periode = ?
        """, (id_periode,)).fetchall():

            nilai_rata_rata[row["id_periode_siswa"]] = row["nilai"]

        # ----------------------------------------------
        # AMBIL NILAI UTS, UAS, KEHADIRAN
        # ----------------------------------------------
        nilai_komponen = {}

        for row in conn.execute("""
            SELECT
                nk.id_periode_siswa,
                nk.nilai_uts,
                nk.nilai_uas,
                nk.nilai_kehadiran
            FROM nilai_komponen nk
            JOIN periode_siswa ps
                ON nk.id_periode_siswa = ps.id_periode_siswa
            WHERE ps.id_periode = ?
        """, (id_periode,)).fetchall():

            nilai_komponen[row["id_periode_siswa"]] = row

        # ----------------------------------------------
        # HITUNG RATA-RATA TUGAS DAN NILAI AKHIR
        # ----------------------------------------------
        hasil_nilai = {}

        for siswa in daftar_siswa:
            id_roster = siswa["id_periode_siswa"]

            if pengaturan["metode_tugas"] == "satu_per_satu":
                daftar_nilai = [
                    nilai_tugas.get((id_roster, tugas["id_tugas"]))
                    for tugas in daftar_tugas
                ]

                nilai_terisi = [
                    nilai for nilai in daftar_nilai
                    if nilai is not None
                ]

                nilai_tugas_akhir = (
                    sum(nilai_terisi) / len(nilai_terisi)
                    if nilai_terisi else None
                )

            else:
                nilai_tugas_akhir = nilai_rata_rata.get(id_roster)

            komponen = nilai_komponen.get(id_roster)

            nilai_uts = (
                komponen["nilai_uts"] if komponen else None
            )
            nilai_uas = (
                komponen["nilai_uas"] if komponen else None
            )
            nilai_kehadiran = (
                komponen["nilai_kehadiran"] if komponen else None
            )

            komponen_lengkap = all(
                nilai is not None
                for nilai in [
                    nilai_tugas_akhir,
                    nilai_uts,
                    nilai_uas,
                    nilai_kehadiran
                ]
            )

            nilai_akhir = None
            grade = None
            status_kkm = None

            if komponen_lengkap:
                nilai_akhir = (
                    nilai_tugas_akhir * pengaturan["bobot_tugas"]
                    + nilai_uts * pengaturan["bobot_uts"]
                    + nilai_uas * pengaturan["bobot_uas"]
                    + nilai_kehadiran * pengaturan["bobot_kehadiran"]
                ) / 100

                if nilai_akhir >= pengaturan["batas_a"]:
                    grade = "A"
                elif nilai_akhir >= pengaturan["batas_b"]:
                    grade = "B"
                elif nilai_akhir >= pengaturan["batas_c"]:
                    grade = "C"
                elif nilai_akhir >= pengaturan["batas_d"]:
                    grade = "D"
                else:
                    grade = "E"

                status_kkm = (
                    "Tuntas"
                    if nilai_akhir >= pengaturan["kkm"]
                    else "Belum Tuntas"
                )

            hasil_nilai[id_roster] = {
                "nilai_tugas": nilai_tugas_akhir,
                "nilai_uts": nilai_uts,
                "nilai_uas": nilai_uas,
                "nilai_kehadiran": nilai_kehadiran,
                "nilai_akhir": nilai_akhir,
                "grade": grade,
                "status_kkm": status_kkm
            }

        kelas_info = conn.execute("""
            SELECT k.nama_kelas, m.nama_mata_pelajaran
            FROM periode_akademik p
            JOIN kelas k ON p.id_kelas = k.id_kelas
            JOIN mata_pelajaran m ON k.id_mata_pelajaran = m.id_mata_pelajaran
            WHERE p.id_periode = ?
        """, (id_periode,)).fetchone()

        nama_kelas = kelas_info["nama_kelas"] if kelas_info else ""
        nama_mapel = kelas_info["nama_mata_pelajaran"] if kelas_info else ""

        return render_template(
            "penilaian/penilaian.html",
            periode=periode,
            pengaturan=pengaturan,
            daftar_siswa=daftar_siswa,
            daftar_tugas=daftar_tugas,
            nilai_tugas=nilai_tugas,
            nilai_rata_rata=nilai_rata_rata,
            nilai_komponen=nilai_komponen,
            hasil_nilai=hasil_nilai,
            nama_guru=session.get("nama_guru", "Guru"),
            kelas_aktif=nama_kelas,
            mata_pelajaran_aktif=nama_mapel
        )

    finally:
        conn.close()


@app.route("/rekapitulasi")
@login_required
def rekapitulasi_nilai():
    id_periode = session.get("id_periode")
    periode = ambil_periode(id_periode)
    if not periode:
        periode = pastikan_periode_aktif()
        if periode:
            id_periode = periode["id_periode"]

    if not periode:
        flash("Pilih atau buat kelas dan periode akademik terlebih dahulu.")
        return redirect(url_for("kelola_kelas"))

    conn = get_db_connection()

    try:
        # AMBIL PENGATURAN PENILAIAN
        pengaturan = conn.execute("""
            SELECT *
            FROM pengaturan_penilaian
            WHERE id_periode = ?
        """, (id_periode,)).fetchone()

        if not pengaturan or pengaturan["bobot_sudah_diatur"] != 1:
            flash("Bobot penilaian belum diatur.")
            return redirect(url_for("dashboard"))

        if pengaturan["grade_sudah_diatur"] != 1:
            flash("Batas predikat dan ketuntasan minimum belum diatur.")
            return redirect(url_for("dashboard"))

        # AMBIL DAFTAR SISWA AKTIF
        daftar_siswa = conn.execute("""
            SELECT
                ps.id_periode_siswa,
                ps.no_absen,
                s.nama_siswa
            FROM periode_siswa ps
            JOIN siswa s
                ON ps.id_siswa = s.id_siswa
            WHERE ps.id_periode = ?
              AND ps.status_aktif = 1
            ORDER BY ps.no_absen ASC
        """, (id_periode,)).fetchall()

        # AMBIL DAFTAR TUGAS
        daftar_tugas = conn.execute("""
            SELECT id_tugas
            FROM tugas
            WHERE id_periode = ?
            ORDER BY id_tugas ASC
        """, (id_periode,)).fetchall()

        # AMBIL NILAI TUGAS SATU PER SATU
        nilai_tugas = {}

        for row in conn.execute("""
            SELECT
                nt.id_periode_siswa,
                nt.id_tugas,
                nt.nilai
            FROM nilai_tugas nt
            JOIN tugas t
                ON nt.id_tugas = t.id_tugas
            WHERE t.id_periode = ?
        """, (id_periode,)).fetchall():

            nilai_tugas[
                (row["id_periode_siswa"], row["id_tugas"])
            ] = row["nilai"]

        # AMBIL NILAI TUGAS RATA-RATA
        nilai_rata_rata = {}

        for row in conn.execute("""
            SELECT
                ntr.id_periode_siswa,
                ntr.nilai
            FROM nilai_tugas_rata_rata ntr
            JOIN periode_siswa ps
                ON ntr.id_periode_siswa = ps.id_periode_siswa
            WHERE ps.id_periode = ?
        """, (id_periode,)).fetchall():

            nilai_rata_rata[row["id_periode_siswa"]] = row["nilai"]

        # AMBIL NILAI UTS, UAS, DAN KEHADIRAN
        nilai_komponen = {}

        for row in conn.execute("""
            SELECT
                nk.id_periode_siswa,
                nk.nilai_uts,
                nk.nilai_uas,
                nk.nilai_kehadiran
            FROM nilai_komponen nk
            JOIN periode_siswa ps
                ON nk.id_periode_siswa = ps.id_periode_siswa
            WHERE ps.id_periode = ?
        """, (id_periode,)).fetchall():

            nilai_komponen[row["id_periode_siswa"]] = row

        # HITUNG HASIL REKAPITULASI
        hasil_rekap = []
        daftar_nilai_lengkap = []
        semua_nilai_tugas = []
        semua_nilai_uts = []
        semua_nilai_uas = []
        semua_nilai_kehadiran = []

        jumlah_tuntas = 0
        jumlah_belum_tuntas = 0
        jumlah_belum_lengkap = 0

        for siswa in daftar_siswa:
            id_roster = siswa["id_periode_siswa"]

            # Hitung nilai tugas sesuai metode aktif
            if pengaturan["metode_tugas"] == "satu_per_satu":
                daftar_nilai_tugas = [
                    nilai_tugas.get(
                        (id_roster, tugas["id_tugas"])
                    )
                    for tugas in daftar_tugas
                ]

                nilai_terisi = [
                    nilai for nilai in daftar_nilai_tugas
                    if nilai is not None
                ]

                nilai_tugas_akhir = (
                    sum(nilai_terisi) / len(nilai_terisi)
                    if nilai_terisi else None
                )

            else:
                nilai_tugas_akhir = nilai_rata_rata.get(id_roster)

            # Ambil komponen lainnya
            komponen = nilai_komponen.get(id_roster)

            nilai_uts = (
                komponen["nilai_uts"] if komponen else None
            )
            nilai_uas = (
                komponen["nilai_uas"] if komponen else None
            )
            nilai_kehadiran = (
                komponen["nilai_kehadiran"] if komponen else None
            )

            if nilai_tugas_akhir is not None:
                semua_nilai_tugas.append(nilai_tugas_akhir)
            if nilai_uts is not None:
                semua_nilai_uts.append(nilai_uts)
            if nilai_uas is not None:
                semua_nilai_uas.append(nilai_uas)
            if nilai_kehadiran is not None:
                semua_nilai_kehadiran.append(nilai_kehadiran)

            lengkap = all(
                nilai is not None
                for nilai in [
                    nilai_tugas_akhir,
                    nilai_uts,
                    nilai_uas,
                    nilai_kehadiran
                ]
            )

            nilai_akhir = None
            grade = None
            status_kkm = None

            if lengkap:
                nilai_akhir = (
                    nilai_tugas_akhir * pengaturan["bobot_tugas"]
                    + nilai_uts * pengaturan["bobot_uts"]
                    + nilai_uas * pengaturan["bobot_uas"]
                    + nilai_kehadiran * pengaturan["bobot_kehadiran"]
                ) / 100

                if nilai_akhir >= pengaturan["batas_a"]:
                    grade = "A"
                elif nilai_akhir >= pengaturan["batas_b"]:
                    grade = "B"
                elif nilai_akhir >= pengaturan["batas_c"]:
                    grade = "C"
                elif nilai_akhir >= pengaturan["batas_d"]:
                    grade = "D"
                else:
                    grade = "E"

                if nilai_akhir >= pengaturan["kkm"]:
                    status_kkm = "Tuntas"
                    jumlah_tuntas += 1
                else:
                    status_kkm = "Belum Tuntas"
                    jumlah_belum_tuntas += 1

            else:
                jumlah_belum_lengkap += 1

            data_siswa = {
                "no_absen": siswa["no_absen"],
                "nama_siswa": siswa["nama_siswa"],
                "nilai_tugas": nilai_tugas_akhir,
                "nilai_uts": nilai_uts,
                "nilai_uas": nilai_uas,
                "nilai_kehadiran": nilai_kehadiran,
                "nilai_akhir": nilai_akhir,
                "grade": grade,
                "status_kkm": status_kkm
            }

            hasil_rekap.append(data_siswa)

            if nilai_akhir is not None:
                daftar_nilai_lengkap.append(data_siswa)

        # HITUNG STATISTIK KELAS
        jumlah_siswa = len(daftar_siswa)
        jumlah_nilai_lengkap = len(daftar_nilai_lengkap)

        rata_rata_kelas = (
            sum(s["nilai_akhir"] for s in daftar_nilai_lengkap)
            / jumlah_nilai_lengkap
            if jumlah_nilai_lengkap else None
        )

        siswa_tertinggi = (
            max(
                daftar_nilai_lengkap,
                key=lambda s: s["nilai_akhir"]
            )
            if daftar_nilai_lengkap else None
        )

        siswa_terendah = (
            min(
                daftar_nilai_lengkap,
                key=lambda s: s["nilai_akhir"]
            )
            if daftar_nilai_lengkap else None
        )

        # Hitung rata-rata per komponen
        rata_tugas = (sum(semua_nilai_tugas) / len(semua_nilai_tugas)) if semua_nilai_tugas else None
        rata_uts = (sum(semua_nilai_uts) / len(semua_nilai_uts)) if semua_nilai_uts else None
        rata_uas = (sum(semua_nilai_uas) / len(semua_nilai_uas)) if semua_nilai_uas else None
        rata_kehadiran = (sum(semua_nilai_kehadiran) / len(semua_nilai_kehadiran)) if semua_nilai_kehadiran else None

        kkm = pengaturan["kkm"] if (pengaturan and pengaturan["kkm"] is not None) else 75
        batas_a = pengaturan["batas_a"] if (pengaturan and pengaturan["batas_a"] is not None) else 85
        batas_b = pengaturan["batas_b"] if (pengaturan and pengaturan["batas_b"] is not None) else 75
        batas_c = pengaturan["batas_c"] if (pengaturan and pengaturan["batas_c"] is not None) else 65
        batas_d = pengaturan["batas_d"] if (pengaturan and pengaturan["batas_d"] is not None) else 50

        if rata_rata_kelas is not None:
            if rata_rata_kelas >= batas_a:
                kategori_rata_rata = "A"
            elif rata_rata_kelas >= batas_b:
                kategori_rata_rata = "B"
            elif rata_rata_kelas >= batas_c:
                kategori_rata_rata = "C"
            elif rata_rata_kelas >= batas_d:
                kategori_rata_rata = "D"
            else:
                kategori_rata_rata = "E"
        else:
            kategori_rata_rata = "-"

        # Persentase ketuntasan
        jumlah_belum_tuntas_tampil = max(0, jumlah_siswa - jumlah_tuntas)
        persen_tuntas = (jumlah_tuntas / jumlah_siswa * 100) if jumlah_siswa else 0
        persen_belum_tuntas = (jumlah_belum_tuntas_tampil / jumlah_siswa * 100) if jumlah_siswa else 0

        # Distribusi Grade
        distribusi_grade = {
            "A": sum(1 for s in daftar_nilai_lengkap if s["grade"] == "A"),
            "B": sum(1 for s in daftar_nilai_lengkap if s["grade"] == "B"),
            "C": sum(1 for s in daftar_nilai_lengkap if s["grade"] == "C"),
            "D": sum(1 for s in daftar_nilai_lengkap if s["grade"] == "D"),
            "E": sum(1 for s in daftar_nilai_lengkap if s["grade"] == "E"),
        }

        # Distribusi Rentang Nilai Akhir
        distribusi_rentang = {
            "0-49": sum(1 for s in daftar_nilai_lengkap if s["nilai_akhir"] < 50),
            "50-59": sum(1 for s in daftar_nilai_lengkap if 50 <= s["nilai_akhir"] < 60),
            "60-69": sum(1 for s in daftar_nilai_lengkap if 60 <= s["nilai_akhir"] < 70),
            "70-79": sum(1 for s in daftar_nilai_lengkap if 70 <= s["nilai_akhir"] < 80),
            "80-89": sum(1 for s in daftar_nilai_lengkap if 80 <= s["nilai_akhir"] < 90),
            "90-100": sum(1 for s in daftar_nilai_lengkap if 90 <= s["nilai_akhir"] <= 100),
        }

        kelas_info = conn.execute("""
            SELECT k.nama_kelas, m.nama_mata_pelajaran
            FROM kelas k
            JOIN mata_pelajaran m ON k.id_mata_pelajaran = m.id_mata_pelajaran
            WHERE k.id_kelas = ?
        """, (periode["id_kelas"],)).fetchone()

        daftar_periode_kelas = conn.execute("""
            SELECT id_periode, semester, tahun_ajaran, status_aktif
            FROM periode_akademik
            WHERE id_kelas = ?
            ORDER BY id_periode DESC
        """, (periode["id_kelas"],)).fetchall()

        return render_template(
            "rekapitulasi/rekapitulasi.html",
            periode=periode,
            kelas_info=kelas_info,
            daftar_periode_kelas=daftar_periode_kelas,
            hasil_rekap=hasil_rekap,
            jumlah_siswa=jumlah_siswa,
            jumlah_nilai_lengkap=jumlah_nilai_lengkap,
            jumlah_tuntas=jumlah_tuntas,
            jumlah_belum_tuntas=jumlah_belum_tuntas_tampil,
            jumlah_belum_lengkap=jumlah_belum_lengkap,
            persen_tuntas=persen_tuntas,
            persen_belum_tuntas=persen_belum_tuntas,
            rata_rata_kelas=rata_rata_kelas,
            kategori_rata_rata=kategori_rata_rata,
            siswa_tertinggi=siswa_tertinggi,
            siswa_terendah=siswa_terendah,
            kkm=kkm,
            rata_tugas=rata_tugas,
            rata_uts=rata_uts,
            rata_uas=rata_uas,
            rata_kehadiran=rata_kehadiran,
            distribusi_grade=distribusi_grade,
            distribusi_rentang=distribusi_rentang
        )

    finally:
        conn.close()


if __name__ == "__main__":
    app.run(debug=True)
