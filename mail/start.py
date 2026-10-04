"""메일은 실습 도메인으로만 배달한다. 인증 성공도 외부 릴레이를 허용하지 않는다."""
import json
import os
from pathlib import Path
import re
import subprocess
from domain import valid_domain


def run(*args): subprocess.run(args, check=True)
def write(path, data): Path(path).write_text(data)
domain = valid_domain(os.environ.get('LAB_DOMAIN', 'kt66.lab'))
accounts = json.loads(Path('/config/accounts.json').read_text())
if not accounts: raise SystemExit('메일 계정이 없습니다.')
for name, digest in accounts.items():
    if not re.fullmatch(r'[a-z][a-z0-9._-]{0,31}', name) or not re.fullmatch(r'\{SHA512-CRYPT\}\$6\$[A-Za-z0-9./$]+', digest):
        raise SystemExit('잘못된 메일 계정 또는 해시입니다.')
run('ip', 'route', 'replace', 'default', 'via', '10.20.32.1')
cert = Path('/var/lib/kt66-mail')
cert.mkdir(parents=True, exist_ok=True)
if not all((cert/name).exists() for name in ('domain','server.crt','server.key')) or (cert/'domain').read_text() != domain:
    run('openssl', 'req', '-x509', '-nodes', '-newkey', 'rsa:3072', '-days', '730',
        '-subj', f'/CN=mail.{domain}/O=KT66 Training', '-addext', f'subjectAltName=DNS:mail.{domain},DNS:mail,IP:10.20.32.25',
        '-keyout', str(cert/'server.key'), '-out', str(cert/'server.crt'))
    (cert/'server.key').chmod(0o600)
    write(cert/'domain', domain)
Path('/var/mail/vhosts').mkdir(parents=True, exist_ok=True)
run('chown', '-R', 'vmail:vmail', '/var/mail/vhosts')
write('/etc/dovecot/users', ''.join(f'{name}@{domain}:{digest}\n' for name,digest in accounts.items()))
run('chown', 'root:dovecot', '/etc/dovecot/users')
Path('/etc/dovecot/users').chmod(0o640)
write('/etc/dovecot/dovecot.conf', '''protocols = imap lmtp
listen = *
ssl = required
ssl_cert = </var/lib/kt66-mail/server.crt
ssl_key = </var/lib/kt66-mail/server.key
disable_plaintext_auth = yes
auth_mechanisms = plain login
auth_username_format = %Lu
mail_location = maildir:/var/mail/vhosts/%n/Maildir
mail_uid = vmail
mail_gid = vmail
first_valid_uid = 5000
log_path = syslog
passdb {
  driver = passwd-file
  args = /etc/dovecot/users
}
userdb {
  driver = static
  args = uid=vmail gid=vmail home=/var/mail/vhosts/%n
}
service imap-login {
  inet_listener imap {
    port = 0
  }
  inet_listener imaps {
    port = 993
    ssl = yes
  }
}
service lmtp {
  unix_listener /var/spool/postfix/private/dovecot-lmtp {
    mode = 0600
    user = postfix
    group = postfix
  }
}
service auth {
  unix_listener /var/spool/postfix/private/auth {
    mode = 0660
    user = postfix
    group = postfix
  }
}
namespace inbox {
  inbox = yes
  mailbox Sent {
    auto = subscribe
    special_use = \\Sent
  }
  mailbox Drafts {
    auto = subscribe
    special_use = \\Drafts
  }
  mailbox Trash {
    auto = subscribe
    special_use = \\Trash
  }
}
''')
write('/etc/postfix/vmailbox', ''.join(f'{name}@{domain} {name}/\n' for name in accounts))
run('postmap', '/etc/postfix/vmailbox')
config = {
 'compatibility_level':'3.6','myhostname':f'mail.{domain}','mydomain':domain,'myorigin':domain,
 'mydestination':'localhost', 'inet_interfaces':'all', 'inet_protocols':'ipv4', 'mynetworks':'127.0.0.0/8',
 'virtual_mailbox_domains':domain, 'virtual_mailbox_maps':'hash:/etc/postfix/vmailbox',
 'virtual_transport':'lmtp:unix:private/dovecot-lmtp', 'relay_domains':'',
 'smtpd_relay_restrictions':'reject_unauth_destination',
 'smtpd_recipient_restrictions':'reject_non_fqdn_recipient,reject_unknown_recipient_domain,reject_unlisted_recipient',
 'smtpd_reject_unlisted_recipient':'yes', 'disable_vrfy_command':'yes',
 'default_transport':'error:5.7.1 External delivery disabled in KT66 training',
 'relay_transport':'error:5.7.1 External delivery disabled in KT66 training',
 'smtpd_tls_cert_file':str(cert/'server.crt'), 'smtpd_tls_key_file':str(cert/'server.key'),
 'smtpd_tls_security_level':'may', 'smtpd_tls_auth_only':'yes',
 'smtpd_sasl_type':'dovecot', 'smtpd_sasl_path':'private/auth', 'smtpd_sasl_auth_enable':'no',
 'message_size_limit':'15728640', 'smtpd_client_connection_rate_limit':'60',
 'smtpd_banner':f'mail.{domain} ESMTP KT66',
}
for key,value in config.items(): run('postconf', '-e', f'{key} = {value}')
# 非 chroot: syslog/auth socket과 TLS 경로를 단일 네임스페이스에서 사용.
write('/etc/postfix/master.cf', '''smtp inet n - n - - smtpd
submission inet n - n - - smtpd
  -o syslog_name=postfix/submission
  -o smtpd_tls_security_level=encrypt
  -o smtpd_sasl_auth_enable=yes
  -o smtpd_client_restrictions=permit_sasl_authenticated,reject
pickup unix n - n 60 1 pickup
cleanup unix n - n - 0 cleanup
qmgr unix n - n 300 1 qmgr
tlsmgr unix - - n 1000? 1 tlsmgr
rewrite unix - - n - - trivial-rewrite
bounce unix - - n - 0 bounce
defer unix - - n - 0 bounce
trace unix - - n - 0 bounce
verify unix - - n - 1 verify
flush unix n - n 1000? 0 flush
proxymap unix - - n - - proxymap
proxywrite unix - - n - 1 proxymap
smtp unix - - n - - smtp
relay unix - - n - - smtp
error unix - - n - - error
retry unix - - n - - error
discard unix - - n - - discard
local unix - n n - - local
virtual unix - n n - - virtual
lmtp unix - - n - - lmtp
anvil unix - - n - 1 anvil
scache unix - - n - 1 scache
postlog unix-dgram n - n - 1 postlogd
''')
write('/etc/rsyslog.conf', '''module(load="imuxsock")
$ActionFileDefaultTemplate RSYSLOG_TraditionalFileFormat
*.* /var/log/mail.log
mail.* @10.20.32.100:514;RSYSLOG_TraditionalForwardFormat
''')
write('/etc/supervisord.conf', '''[supervisord]
nodaemon=true
user=root
logfile=/dev/null
pidfile=/run/supervisord.pid
[program:syslog]
command=/usr/sbin/rsyslogd -n
priority=10
autorestart=true
[program:dovecot]
command=/usr/sbin/dovecot -F
priority=20
autorestart=true
[program:postfix]
command=/usr/sbin/postfix start-fg
priority=30
autorestart=true
stopasgroup=true
killasgroup=true
[program:logs]
command=/usr/bin/tail -F /var/log/mail.log
stdout_logfile=/dev/fd/1
stdout_logfile_maxbytes=0
stderr_logfile=/dev/fd/2
stderr_logfile_maxbytes=0
''')
run('postfix', 'check')
os.execvp('supervisord', ['supervisord','-c','/etc/supervisord.conf'])
