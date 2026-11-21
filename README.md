# GRADE MASTER V2

Aplikasi web penilaian siswa (Flask) untuk guru: pengelolaan mata pelajaran, kelas, periode akademik, data siswa, penilaian tugas/UTS/UAS/kehadiran, rekapitulasi, dan ekspor CSV/PDF dari halaman rekapitulasi.

## Teknologi

- Python 3.12, Flask
- PostgreSQL (direkomendasikan Neon) via `psycopg`
- Frontend: template Jinja, CSS/JS statis, Chart.js lokal (`static/js/chart.umd.min.js`)

## Persyaratan

- Python 3.12 (lihat `.python-version`)
- Basis data PostgreSQL (Neon, dsb.)

## Instalasi lokal

```bash
python -m venv .venv
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Salin variabel lingkungan (buat berkas `.env` di root project):

```env
DATABASE_URL=postgresql://...
SECRET_KEY=ganti-dengan-nilai-acak-panjang
```

- `DATABASE_URL` wajib.
- `SECRET_KEY` wajib (tanpa nilai default di production). Di Vercel, atur lewat Environment Variables.

Jalankan aplikasi:

```bash
python app.py
```

Aplikasi membuat tabel otomatis saat startup lewat `database.buat_tabel()`.

## Pemeriksaan kesehatan

- `GET /healthz` mengembalikan status `database`, `psycopg`, dan `skema_awal` (JSON).

## Tes

```bash
python -m unittest discover -s tests -v
```

Di CI, set `SECRET_KEY` (contoh: nilai dummy) agar import aplikasi berhasil.

## Deploy (ringkas)

- Deploy ke Vercel (`vercel.json`) menggunakan `app.py` sebagai serverless function.
- Pastikan variabel `DATABASE_URL` dan `SECRET_KEY` tersedia di project Vercel (Production/Preview).

## Catatan keamanan (sudah diterapkan)

- `SECRET_KEY` wajib, cookie sesi `HttpOnly`/`SameSite=Lax`, `Secure` aktif saat `VERCEL=1`.
- CSRF token pada semua form POST.
- Batas percobaan login (rate limit in-memory).
- Header keamanan dan halaman error kustom.

## Lisensi

Lihat berkas [LICENSE](LICENSE).
