"""최초 배포 시 전용 인덱스·역할만 준비한다. 관리자 인증서는 이 일회성 컨테이너만 읽는다."""
import datetime as dt
import json
import os
from pathlib import Path
import secrets
import time
from client import IndexClient, IndexErrorResponse
from projection import EVENTS, FINDINGS, TICKETS
from schema import mappings, VERSION


def credentials(folder, username):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'account.json'
    if path.exists():
        account = json.loads(path.read_text())
        if account.get('username') != username or not account.get('password'):
            raise ValueError('전용 계정 파일을 확인하세요')
    else:
        account = {'username': username, 'password': secrets.token_urlsafe(36)}
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(account, output)
    uid, gid = int(os.environ.get('OBS_UID', 1000)), int(os.environ.get('OBS_GID', 1000))
    os.chown(folder, uid, gid)
    os.chmod(folder, 0o700)
    os.chown(path, uid, gid)
    os.chmod(path, 0o600)
    return account


def configure(client, writer, reader):
    mapping = mappings()
    patterns = [EVENTS + '*', FINDINGS, TICKETS]
    client.request('PUT', '/_index_template/kt66-agent-observability-v1', {
        'index_patterns': patterns, 'priority': 150,
        'template': {'settings': {'number_of_shards': 1, 'number_of_replicas': 0, 'refresh_interval': '5s'},
                     'mappings': mapping},
        '_meta': {'description': 'KT66 에이전트 관제. 보존 기한은 강사가 결정하며 자동 삭제하지 않음', 'schema': VERSION}})
    for suffix, account, permissions, cluster in [
        ('writer', writer, [{'index_patterns': patterns, 'allowed_actions': [
            'indices:data/write/bulk*', 'indices:data/write/index*', 'indices:admin/create', 'indices:admin/mapping/put']}], ['indices:data/write/bulk']),
        ('reader', reader, [{'index_patterns': patterns + ['wazuh-alerts-*'],
                            'allowed_actions': ['indices:data/read/search*', 'indices:data/read/get*']}], []),
    ]:
        role = 'kt66_observer_' + suffix
        client.request('PUT', '/_plugins/_security/api/roles/' + role,
                       {'cluster_permissions': cluster, 'index_permissions': permissions, 'tenant_permissions': []})
        client.request('PUT', '/_plugins/_security/api/internalusers/' + account['username'],
                       {'password': account['password'], 'backend_roles': [], 'attributes': {'purpose': 'kt66-observability'}})
        client.request('PUT', '/_plugins/_security/api/rolesmapping/' + role,
                       {'users': [account['username']], 'backend_roles': [], 'hosts': []})
    for index in [EVENTS + dt.datetime.now(dt.timezone.utc).strftime('%Y.%m.%d'), FINDINGS, TICKETS]:
        try:
            client.request('GET', '/' + index + '/_settings')
        except IndexErrorResponse as error:
            if error.status != 404:
                raise
            client.request('PUT', '/' + index, {})
    # 템플릿 변경은 기존 인덱스에 소급되지 않는다. 기존 필드의 형식은 보존하며
    # 정확히 KT66 세 패턴에만 새 매핑을 먼저 적용한 뒤 수집기를 시작한다.
    client.request('PUT', '/' + ','.join(patterns) + '/_mapping', mapping)


if __name__ == '__main__':
    client = IndexClient(certificate=('/certs/admin.pem', '/certs/admin-key.pem'))
    writer = credentials('/writer', 'kt66_observer_writer')
    reader = credentials('/reader', 'kt66_observer_reader')
    Path('/state').mkdir(exist_ok=True)
    os.chown('/state', int(os.environ.get('OBS_UID', 1000)), int(os.environ.get('OBS_GID', 1000)))
    os.chmod('/state', 0o700)
    for attempt in range(36):
        try:
            configure(client, writer, reader)
            print('에이전트 SIEM 인덱스와 분리된 수집·조회 계정 준비 완료', flush=True)
            break
        except IndexErrorResponse as error:
            print(str(error) + ' · 초기화 재시도 ' + str(attempt + 1), flush=True)
            if attempt == 35:
                raise SystemExit(1)
            time.sleep(5)
