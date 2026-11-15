from app import app
import os
import subprocess

client = app.test_client()
with client.session_transaction() as sess:
    sess['id_guru'] = 1
    sess['nama_guru'] = 'Reva'
    sess['id_mata_pelajaran'] = 1

resp = client.get('/dashboard')
html = resp.data.decode('utf-8')
# Fix relative asset links for file:// protocol
html = html.replace('/static/', './')
with open('static/rendered_dashboard.html', 'w', encoding='utf-8') as f:
    f.write(html)
print('Saved rendered_dashboard.html, size:', len(html))

brave_path = r'C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe'
file_url = 'file:///' + os.path.abspath('static/rendered_dashboard.html').replace('\\', '/')
cmd = [
    brave_path,
    '--headless',
    '--disable-gpu',
    '--window-size=1440,1050',
    '--screenshot=' + os.path.abspath('static/dashboard_preview.png'),
    file_url
]
subprocess.run(cmd, capture_output=True)
print('Screenshot taken: static/dashboard_preview.png')
