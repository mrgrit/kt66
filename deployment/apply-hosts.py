#!/usr/bin/env python3
"""호스트의 KT66 관리 블록만 갱신한다. 다른 hosts 항목은 보존한다."""
from pathlib import Path
import re
from configure import ROOT
START = '# BEGIN KT66 LAB DOMAIN'
END = '# END KT66 LAB DOMAIN'


def updated_hosts(current, records):
    current=re.sub(r'(?m)^'+re.escape(START)+r'\n.*?^'+re.escape(END)+r'\n?', '', current, flags=re.S)
    return current.rstrip()+'\n\n'+START+'\n'+records.strip()+'\n'+END+'\n'


if __name__=='__main__':
    hosts=Path('/etc/hosts')
    hosts.write_text(updated_hosts(hosts.read_text(),(ROOT/'deployment/runtime/hosts.txt').read_text()))
    print('[kt66] 서버 hosts의 KT66 도메인 블록 갱신 완료')
