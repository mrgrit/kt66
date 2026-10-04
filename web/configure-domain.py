#!/usr/bin/env python3
"""읽기 전용 원본 vhost에서 현재 도메인용 활성 설정을 매번 재생성한다."""
import os
from pathlib import Path
import re
import subprocess

domain = os.environ.get('LAB_DOMAIN', 'kt66.lab').lower()
if len(domain)>253 or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?',domain) or '..' in domain or '.' not in domain:
    raise SystemExit('잘못된 LAB_DOMAIN')
active = Path('/etc/apache2/sites-enabled')
for old in active.glob('*.conf'):
    if not (Path('/etc/apache2/sites-available')/old.name).exists(): old.unlink()
for source in Path('/etc/apache2/sites-available').glob('*.conf'):
    target = active/source.name
    if target.is_symlink(): target.unlink()
    target.write_text(source.read_text().replace('kt66.lab',domain))
cert = Path('/etc/apache2/ssl')
cert.mkdir(parents=True,exist_ok=True)
if not (cert/'kt66-domain').exists() or (cert/'kt66-domain').read_text()!=domain:
    subprocess.run(['openssl','req','-x509','-nodes','-days','730','-newkey','rsa:3072',
        '-keyout',str(cert/'server.key'),'-out',str(cert/'server.crt'),
        '-subj',f'/CN=*.{domain}/O=KT66/C=KR','-addext',f'subjectAltName=DNS:{domain},DNS:*.{domain}'],check=True)
    (cert/'server.key').chmod(0o600)
    (cert/'kt66-domain').write_text(domain)
print('[web] 내부 도메인 vhost·TLS 적용:', domain)
