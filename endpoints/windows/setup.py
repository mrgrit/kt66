#!/usr/bin/env python3
"""Windows 평가판의 전용 사용자망을 구성하고 선택 Compose 서비스를 시작한다."""
import json
from pathlib import Path
import secrets
import subprocess
from download import checksum, URL

ROOT = Path(__file__).resolve().parents[2]


def run(*args, input=None, capture=False):
    return subprocess.run(args, input=input, text=True, capture_output=capture, check=True, cwd=ROOT).stdout


def main():
    if not Path('/dev/kvm').exists():
        raise SystemExit('호스트 KVM이 필요합니다. 가상화 없는 소프트웨어 에뮬레이션으로 전환하지 않습니다.')
    env = ROOT / '.env'
    iso = ROOT / 'endpoints/windows/media/windows-eval.iso'
    provenance = iso.parent / 'source.json'
    if not iso.is_file() or not provenance.is_file():
        raise SystemExit('먼저 python3 endpoints/windows/download.py로 공식 평가판을 준비하세요.')
    source = json.loads(provenance.read_text())
    if source.get('url') != URL or iso.stat().st_size != source.get('bytes') or checksum(iso) != source.get('sha256'):
        raise SystemExit('공식 ISO의 출처·크기·SHA-256이 준비 기록과 다릅니다.')
    lines = env.read_text().splitlines()
    if not any(line.startswith('WINDOWS_PASSWORD=') and line.split('=', 1)[1].strip() for line in lines):
        with env.open('a') as stream:
            stream.write('\n# Windows 평가판 및 인증된 설치 콘솔 전용\nWINDOWS_PASSWORD=' + secrets.token_urlsafe(24) + '\n')
    run('docker', 'compose', '-f', 'docker-compose.yaml', '--profile', 'windows', 'config', '--quiet')
    network = subprocess.run(['docker', 'network', 'inspect', 'kt66-user'], capture_output=True, text=True)
    if network.returncode:
        run('docker', 'network', 'create', '--internal', '--subnet', '10.20.70.0/24', '--gateway', '10.20.70.254',
            '--label', 'com.docker.compose.project=kt66', '--label', 'com.docker.compose.network=user', 'kt66-user', capture=True)
    else:
        data = json.loads(network.stdout)[0]
        if not data['Internal'] or data['IPAM']['Config'][0]['Subnet'] != '10.20.70.0/24':
            raise SystemExit('기존 kt66-user 네트워크 설정이 달라 중단했습니다.')
    ips = json.loads(run('docker', 'inspect', 'kt66-ips', capture=True))[0]
    if 'kt66-user' not in ips['NetworkSettings']['Networks']:
        run('docker', 'network', 'connect', '--ip', '10.20.70.1', 'kt66-user', 'kt66-ips')
    # 새 브리지 생성 후 프로젝트의 기존 출처 IP 보존 설정을 다시 적용한다.
    run('docker', 'compose', '-f', 'docker-compose.yaml', 'up', '-d', '--no-deps', '--force-recreate', 'netglue')
    script = (ROOT / 'endpoints/user-network.sh').read_text()
    scope_dnat()
    for name in ('fw', 'ips'):
        run('docker', 'exec', '-i', 'kt66-' + name, 'bash', '-s', '--', name, input=script)
    # 성공한 이미지의 다이제스트를 고정하여 다음 기동에서 최신 이미지로 바뀌지 않게 한다.
    configured = next((line.split('=', 1)[1].strip('"\'') for line in lines if line.startswith('WINDOWS_IMAGE=')), '')
    if not configured:
        run('docker', 'pull', 'dockurr/windows:latest')
        image = json.loads(run('docker', 'image', 'inspect', 'dockurr/windows:latest', capture=True))[0]['RepoDigests'][0]
        with env.open('a') as stream:
            stream.write('WINDOWS_IMAGE=' + image + '\n')
    if not any(line.startswith('WINDOWS_CONSOLE_IMAGE=') for line in lines):
        run('docker', 'pull', 'nginx:alpine')
        image = json.loads(run('docker', 'image', 'inspect', 'nginx:alpine', capture=True))[0]['RepoDigests'][0]
        with env.open('a') as stream:
            stream.write('WINDOWS_CONSOLE_IMAGE=' + image + '\n')
    run('docker', 'compose', '-f', 'docker-compose.yaml', '--profile', 'windows', 'up', '-d', '--no-deps', 'windows-user', 'windows-console')
    print('Windows 평가판 설치 시작. 로그인 정보는 .env의 WINDOWS_PASSWORD이며 출력하지 않습니다.')


def scope_dnat():
    """기존 기본 DNAT만 한 트랜잭션으로 제한한다. 사용자 추가·수정 룰은 유지한다."""
    data = json.loads(run('docker', 'exec', 'kt66-fw', 'nft', '-j', 'list', 'chain', 'ip', 'six_nat', 'prerouting', capture=True))
    replacements = []
    for row in data.get('nftables', []):
        rule = row.get('rule', {})
        expr = rule.get('expr', [])
        if len(expr) != 2 or not isinstance(rule.get('handle'), int):
            continue
        match, dnat = expr[0].get('match', {}), expr[1].get('dnat', {})
        port = match.get('right')
        if match.get('left') != {'payload': {'protocol': 'tcp', 'field': 'dport'}} or match.get('op') != '==':
            continue
        target = '10.20.30.201' if port == 9100 else '10.20.32.80'
        if port not in (80, 443, *range(8001, 8008), 9100) or dnat != {'addr': target, 'port': port}:
            continue
        replacements.append(f"replace rule ip six_nat prerouting handle {rule['handle']} ip daddr 10.20.30.1 tcp dport {port} dnat to {target}:{port}")
    if replacements:
        payload = '\n'.join(replacements) + '\n'
        run('docker', 'exec', '-i', 'kt66-fw', 'nft', '--check', '-f', '-', input=payload)
        run('docker', 'exec', '-i', 'kt66-fw', 'nft', '-f', '-', input=payload)
        print(f'기본 DNAT {len(replacements)}개를 FW 수신 주소에 한정했습니다.')


if __name__ == '__main__':
    main()
