#!/usr/bin/env python3
"""실제 실습 계정으로 TLS 송수신·잘못된 인증·외부 릴레이 거부를 검증한다. 비밀 출력 없음."""
from email.message import EmailMessage
from email.utils import make_msgid, formatdate
import imaplib
import json
from pathlib import Path
import smtplib
import ssl
import subprocess
import sys
import tempfile
import time
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'deployment'))
from configure import ROOT,read_env

env=read_env();domain=env['LAB_DOMAIN'];host=env['INT_HOST_IP']
credentials=json.loads((ROOT/'mail/credentials.local.json').read_text())
cert=subprocess.run(['docker','exec','kt66-mail','cat','/var/lib/kt66-mail/server.crt'],text=True,capture_output=True,check=True).stdout
with tempfile.NamedTemporaryFile(mode='w') as file:
    file.write(cert);file.flush()
    context=ssl.create_default_context(cafile=file.name)
    # 접속은 호스트 게시 IP다. 로컬 배포에서 읽은 서버 인증서 자체로 체인을 검증한다.
    context.check_hostname=False
    subject='KT66 mail verification '+uuid.uuid4().hex[:12]
    message=EmailMessage();message['From']=f'instructor@{domain}';message['To']=f'student@{domain}';message['Subject']=subject
    message['Message-ID']=make_msgid(domain=domain);message['Date']=formatdate(localtime=False)
    message.set_content('KT66 내부 메일 송수신 검증입니다. 실제 악성 동작은 포함하지 않습니다.')
    message.add_attachment(b'KT66 benign attachment\n',maintype='text',subtype='plain',filename='training-note.txt')
    with smtplib.SMTP(host,587,timeout=10) as smtp:
        smtp.starttls(context=context)
        try: smtp.login(f'instructor@{domain}','intentionally-invalid-password')
        except smtplib.SMTPAuthenticationError: pass
        else: raise AssertionError('잘못된 비밀번호를 수락함')
        smtp.login(f'instructor@{domain}',credentials['instructor'])
        smtp.send_message(message)
        smtp.mail(f'instructor@{domain}')
        code,_=smtp.rcpt('outside@example.invalid')
        assert code>=500, f'인증된 외부 릴레이가 허용됨: {code}'
    with smtplib.SMTP(host,25,timeout=10) as smtp:
        smtp.mail('training-sender@example.invalid')
        code,_=smtp.rcpt('outside@example.invalid');assert code>=500
        smtp.rset();smtp.mail('training-sender@example.invalid')
        code,_=smtp.rcpt(f'nonexistent@{domain}');assert code>=500
    with imaplib.IMAP4_SSL(host,993,ssl_context=context,timeout=10) as imap:
        imap.login(f'student@{domain}',credentials['student']);imap.select('INBOX')
        for _ in range(10):
            status,ids=imap.search(None,'SUBJECT',f'"{subject}"')
            if ids[0]:break
            time.sleep(1)
        assert ids[0], 'SMTP 메시지가 IMAP 사서함에 도착하지 않음'
        _,body=imap.fetch(ids[0].split()[-1],'(RFC822)')
        assert b'training-note.txt' in body[0][1]
        imap.logout()
print(json.dumps({'smtp_tls':True,'imap_tls':True,'attachment_received':True,'invalid_auth_rejected':True,'external_relay_rejected_authenticated':True,'external_relay_rejected_anonymous':True,'unknown_recipient_rejected':True,'subject':subject},ensure_ascii=False))
