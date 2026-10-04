#!/usr/bin/env python3
"""배포 도메인·DNS·공통 UI 설정을 생성한다. 비밀은 출력하지 않는다."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOMAIN = 'kt66.lab'
NAMES = 'www juice dvwa neobank govportal mediforum admin adminconsole ai aicompanion portal siem bastion assessor fw-gui ips-gui waf-gui noc injector envsim agentops modelops infraops mail webmail windows'.split()


def valid_domain(value):
    value = value.strip().lower().rstrip('.')
    labels = value.split('.')
    if len(value) > 253 or len(labels) < 2 or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', x) for x in labels) or labels[-1].isdigit():
        raise ValueError('도메인은 example.test 같은 영문 FQDN으로 입력하세요. URL·포트·공백은 사용할 수 없습니다.')
    return value


def read_env(root=ROOT):
    values = {}
    for line in (root / '.env').read_text().splitlines():
        if re.match(r'^[A-Z][A-Z0-9_]*=', line):
            key, value = line.split('=', 1)
            values[key] = value.strip().strip('\"\'')
    return values


def persist(root, key, value):
    path = root / '.env'
    lines = path.read_text().splitlines()
    lines = [line for line in lines if not line.startswith(key + '=')]
    lines.append(key + '=' + value)
    path.write_text('\n'.join(lines) + '\n')


def write(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data)
    path.chmod(mode)


def dns_config(domain, web_ip, int_ip, internal=False):
    # 별도 리스너: 컨테이너/Windows는 실제 DMZ 주소, 강의실 PC는 호스트 게시 주소.
    lines = ['no-resolv', 'no-hosts', 'domain-needed', 'bogus-priv', 'bind-interfaces',
             f'port={53 if internal else 1053}', 'user=dnsmasq', 'cache-size=1000',
             'server=1.1.1.1', 'server=1.0.0.1', f'local=/{domain}/',
             f'mx-host={domain},mail.{domain},10', f'txt-record={domain},"v=spf1 -all"']
    for name in [''] + NAMES:
        fqdn = (name + '.' if name else '') + domain
        address = '10.20.32.80' if internal else web_ip
        if name == 'mail': address = '10.20.32.25' if internal else int_ip
        if name == 'windows': address = '10.20.70.10' if internal else int_ip
        lines.append(f'host-record={fqdn},{address}')
    return '\n'.join(lines) + '\n'


def configure(root=ROOT, domain=None, prompt=False):
    env = read_env(root)
    current = env.get('LAB_DOMAIN') or DEFAULT_DOMAIN
    domain = domain or os.environ.get('LAB_DOMAIN')
    if not domain and prompt:
        while True:
            try:
                domain = valid_domain(input(f'  내부 실습 도메인 [기본 {current}]: ') or current)
                break
            except ValueError as error:
                print(error)
    domain = valid_domain(domain or current)
    web = env.get('WEB_HOST_IP') or '127.0.0.1'
    internal = env.get('INT_HOST_IP') or web
    for address in (web, internal):
        ipaddress.IPv4Address(address)
        if address == '0.0.0.0':
            raise ValueError('DNS 주소에는 0.0.0.0을 사용할 수 없습니다. .env에 실제 WEB_HOST_IP / INT_HOST_IP를 지정하세요.')
    persist(root, 'LAB_DOMAIN', domain)
    runtime = root / 'deployment/runtime'
    write(runtime / 'dns-internal.conf', dns_config(domain, web, internal, True))
    write(runtime / 'dns-lan.conf', dns_config(domain, web, internal))
    hosts = [f'{web} {domain} ' + ' '.join(f'{n}.{domain}' for n in NAMES if n not in ('mail', 'windows')),
             f'{internal} mail.{domain} windows.{domain}']
    write(runtime / 'hosts.txt', '\n'.join(hosts) + '\n')
    public = {'domain': domain, 'web_host': web, 'internal_host': internal, 'dns': web,
              'mail': {'host': f'mail.{domain}', 'webmail': f'https://webmail.{domain}/',
                       'submission': 587, 'imaps': 993}}
    write(root / 'ui/deployment.json', json.dumps(public, ensure_ascii=False, indent=2) + '\n')
    accounts = root / 'mail/accounts.json'
    if not accounts.exists():
        initial, hashed = {}, {}
        for name in ('student', 'instructor', 'soc'):
            password = secrets.token_urlsafe(18)
            digest = subprocess.run(['openssl', 'passwd', '-6', '-stdin'], input=password+'\n', text=True, capture_output=True, check=True).stdout.strip()
            hashed[name] = '{SHA512-CRYPT}' + digest
            initial[name] = password
        write(accounts, json.dumps(hashed, indent=2)+'\n', 0o600)
        write(root / 'mail/credentials.local.json', json.dumps(initial, indent=2)+'\n', 0o600)
    key = root / 'mail/roundcube-key'
    if not key.exists(): write(key, secrets.token_hex(12), 0o600)
    print(f'[kt66] 내부 도메인: {domain} / 웹메일: https://webmail.{domain}/')
    print('[kt66] DNS·hosts·메일 계정 준비 완료. 계정 확인: python3 mail/manage.py show')
    return domain


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--domain')
    parser.add_argument('--prompt', action='store_true')
    args = parser.parse_args()
    try: configure(domain=args.domain, prompt=args.prompt)
    except (ValueError, OSError) as error: raise SystemExit(str(error))
