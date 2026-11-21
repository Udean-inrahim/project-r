import os
import unittest

os.environ.setdefault("SECRET_KEY", "test-secret-key")

from app import app


class NavigasiAkunTest(unittest.TestCase):
    def test_sidebar_punya_satu_tautan_akun_dengan_tujuan_lama(self):
        with app.test_request_context():
            from flask import render_template

            html = render_template(
                "components/sidebar.html",
                active_page="pengaturan_akun",
                global_nama_guru="Guru Uji",
            )

        account_links = [
            line for line in html.splitlines()
            if 'href="/akun' in line and 'class="nav-link' in line
        ]
        self.assertEqual(len(account_links), 1)
        self.assertIn('href="/akun/pengaturan"', html)
        self.assertIn(">Pengaturan Akun</span>", html)
        self.assertNotIn(">Profil Guru</span>", html)


if __name__ == "__main__":
    unittest.main()
