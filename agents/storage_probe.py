"""등록된 호스트·컨테이너의 파일시스템 용량을 읽기 전용으로 측정한다."""
import concurrent.futures
import datetime
import os
from pathlib import Path
import re
import subprocess

import yaml


EXCLUDED = {'tmpfs', 'devtmpfs', 'proc', 'sysfs', 'cgroup', 'cgroup2', 'efivarfs',
            'debugfs', 'securityfs', 'pstore', 'devpts', 'squashfs', 'iso9660', 'erofs'}
CONTAINER = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z')


def parse_df(output, threshold):
    """GNU/BusyBox df -PkT의 정수 Use%를 기준으로 비교한다(예약 블록 포함)."""
    rows, excluded = [], 0
    lines = output.splitlines()
    if not lines or not lines[0].startswith('Filesystem'):
        raise ValueError('df 출력 형식을 확인하지 못했습니다')
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split(None, 6)
        if len(fields) != 7:
            raise ValueError('일부 파일시스템 정보를 해석하지 못했습니다')
        device, kind, total, used, available, percent, mount = fields
        if kind in EXCLUDED:
            excluded += 1
            continue
        if not re.fullmatch(r'\d+%', percent):
            raise ValueError('파일시스템 사용률을 확인하지 못했습니다')
        total, used, available = int(total)*1024, int(used)*1024, int(available)*1024
        if total <= 0 or used < 0 or not mount.startswith('/'):
            raise ValueError('잘못된 파일시스템 용량입니다')
        percentage = int(percent[:-1])
        rows.append(dict(filesystem=device, type=kind, mountpoint=mount, total_bytes=total,
                         used_bytes=used, available_bytes=available, used_percent=percentage,
                         at_or_above_threshold=percentage >= threshold))
    return rows, excluded


def targets(root):
    """모델이 명령·호스트 주소를 입력하지 않고 자산 ID로만 대상을 선택한다."""
    root = Path(root)
    layout = yaml.safe_load((root.parent/'envsim/assets.yaml').read_text())
    host = dict(id='host', name='KT66 실행 호스트', kind='host', aliases=[])
    rows, containers = [host], {}
    for asset in layout['it_assets']:
        row = {k: asset.get(k) for k in ('id', 'name')}
        name = asset.get('container')
        if name:
            if not isinstance(name, str) or not CONTAINER.fullmatch(name):
                raise ValueError('자산 대장의 컨테이너 이름이 올바르지 않습니다')
            if name in containers:
                containers[name]['aliases'].append(asset['id'])
                continue
            row.update(kind='container', container=name, aliases=[])
            containers[name] = row
        elif asset.get('storage_source') == 'host':
            host['aliases'].append(asset['id'])
            continue
        else:
            row.update(kind='unconnected', aliases=[], reason='이 자산의 디스크 측정 연결이 없습니다')
        rows.append(row)
    # 자산 대장에 아직 없는 DB·관리 서비스도 배포 명세를 기준으로 포함한다.
    compose = root.parent/'docker-compose.yaml'
    if compose.is_file():
        services = yaml.safe_load(compose.read_text()).get('services', {})
        for service, config in services.items():
            name = config.get('container_name')
            if isinstance(name, str) and CONTAINER.fullmatch(name) and name not in containers:
                row = dict(id=name, name=service, kind='container', container=name, aliases=[])
                containers[name] = row
                rows.append(row)
    if len(rows) > 100:
        raise ValueError('등록된 측정 대상이 100개를 초과합니다. 수집 범위를 구성해야 합니다')
    return rows


def measure(target, threshold):
    result = {**target, 'measured_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'status': 'unavailable', 'filesystems': []}
    if target['kind'] == 'unconnected':
        result['error'] = target['reason']
        return result
    command = (['docker', 'exec', '-e', 'LC_ALL=C', target['container'], 'df', '-PkT']
               if target['kind'] == 'container' else ['df', '-PkT'])
    result['source_command'] = command
    try:
        p = subprocess.run(command, capture_output=True, text=True, timeout=5,
                           env={**os.environ, 'LC_ALL': 'C'})
        result.update(returncode=p.returncode, raw_output=p.stdout[:262144])
        if p.returncode and not p.stdout.startswith('Filesystem'):
            detail = (p.stderr or p.stdout)[:400]
            result['error'] = ('컨테이너가 중지되어 측정할 수 없습니다' if 'not running' in detail else
                               '이 컨테이너 이미지에 df가 없습니다' if 'executable file not found' in detail else
                               '컨테이너가 아직 생성되지 않았습니다' if 'No such container' in detail else
                               '디스크 조회 명령을 실행하지 못했습니다')
            result['error_detail'] = detail
            return result
        rows, ignored = parse_df(p.stdout[:262144], threshold)
        result.update(filesystems=rows, excluded_filesystems=ignored)
        if p.returncode or len(p.stdout) > 262144:
            result.update(status='partial' if rows else 'unavailable', error='일부 파일시스템을 읽지 못했습니다. 원본 기록을 확인하세요')
        elif rows:
            result['status'] = 'measured'
        else:
            result['error'] = '조회 가능한 영구 저장 파일시스템이 없습니다'
    except subprocess.TimeoutExpired:
        result['error'] = '디스크 조회 제한 시간(5초)을 초과했습니다'
    except (OSError, ValueError) as error:
        result['error'] = '디스크 조회 실패: ' + str(error)[:180]
    return result


def collect(root, target='all', threshold_pct=80, allowed_targets=None):
    if type(threshold_pct) is not int or not 1 <= threshold_pct <= 100:
        raise ValueError('사용률 기준은 1~100의 정수입니다')
    catalog = targets(root)
    if allowed_targets is not None:
        from authorization import matches
        catalog = [t for t in catalog if any(matches(v,allowed_targets) for v in [t['id'],t.get('container'),*t['aliases']])]
    if not isinstance(target, str):
        raise ValueError('등록된 자산 ID 또는 all, host를 지정하세요')
    selected = catalog if target == 'all' else [t for t in catalog if target in [t['id'], t.get('container'), *t['aliases']]]
    if not selected:
        raise ValueError('등록된 자산 ID 또는 all, host를 지정하세요')
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        readings = list(pool.map(lambda t: measure(t, threshold_pct), selected))
    measured = [r for r in readings if r['filesystems']]
    incomplete = [dict(id=r['id'], name=r['name'], reason=r.get('error', '측정 불완전'))
                  for r in readings if r['status'] != 'measured']
    matches = [{**{k: r[k] for k in ('id', 'name', 'kind', 'measured_at')}, **fs}
               for r in readings for fs in r['filesystems'] if fs['at_or_above_threshold']]
    return dict(status='ok' if not incomplete else ('partial' if measured else 'unavailable'),
                started_at=started, finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                threshold_pct=threshold_pct, threshold_basis='df Use% 표시값(예약 블록 반영·정수 올림)',
                target_count=len(readings), measured_targets=len(measured), incomplete_targets=incomplete,
                matching_targets=len({m['id'] for m in matches}), matches=matches, targets=readings,
                limitations=['컨테이너 overlay와 바인드 마운트는 호스트 저장 공간을 공유할 수 있습니다. 용량을 합산하거나 개별 물리 디스크로 세지 마세요.',
                             'tmpfs 등 메모리 파일시스템과 squashfs 등 읽기 전용 이미지는 디스크 임계치 판단에서 제외합니다.',
                             '현재 시점 측정입니다. 단일 측정으로 증가율·미래 안정성·지난 시점의 사용률을 판단할 수 없습니다.',
                             '미측정·부분 측정 대상이 남으면 전체 시스템 정상으로 결론 내리지 마세요.'])
