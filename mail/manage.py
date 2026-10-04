#!/usr/bin/env python3
"""교육용 메일 계정 조회·추가·비밀번호 변경. 저장 후 mail 서비스를 재시작한다."""
import argparse
import getpass
import json
from pathlib import Path
import re
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deployment'))
from configure import ROOT, read_env, write

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('action', choices=['show','list','set-password'])
parser.add_argument('username', nargs='?')
args = parser.parse_args()
domain = read_env().get('LAB_DOMAIN','kt66.lab')
path = ROOT/'mail/accounts.json'
accounts = json.loads(path.read_text())
if args.action in ('list','show'):
    credentials = json.loads((ROOT/'mail/credentials.local.json').read_text()) if args.action=='show' else {}
    for name in accounts: print(f'{name}@{domain}' + (f'  {credentials.get(name,"(직접 지정한 비밀번호)")}' if args.action=='show' else ''))
else:
    if not args.username or not re.fullmatch(r'[a-z][a-z0-9._-]{0,31}', args.username): parser.error('영문 소문자 계정 이름이 필요합니다.')
    password = getpass.getpass('새 메일 비밀번호: ')
    if len(password)<12: parser.error('12자 이상 입력하세요.')
    if password != getpass.getpass('비밀번호 확인: '): parser.error('비밀번호가 다릅니다.')
    digest = subprocess.run(['openssl','passwd','-6','-stdin'],input=password+'\n',text=True,capture_output=True,check=True).stdout.strip()
    accounts[args.username] = '{SHA512-CRYPT}'+digest
    write(path,json.dumps(accounts,indent=2)+'\n',0o600)
    initial=ROOT/'mail/credentials.local.json'
    if initial.exists():
        entries=json.loads(initial.read_text()); entries.pop(args.username,None)
        write(initial,json.dumps(entries,indent=2)+'\n',0o600)
    subprocess.run(['docker','compose','restart','mail'],cwd=ROOT,check=True)
    print(f'{args.username}@{domain} 적용 완료')
