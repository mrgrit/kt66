"""헬스체크 자체가 메일 감사 로그를 생성하지 않도록 프로세스와 소켓만 확인한다."""
import os
from pathlib import Path
for name in ('/var/spool/postfix/pid/master.pid','/run/dovecot/master.pid'):
    os.kill(int(Path(name).read_text().strip()),0)
for name in ('/var/spool/postfix/private/auth','/var/spool/postfix/private/dovecot-lmtp'):
    assert Path(name).is_socket(),name
