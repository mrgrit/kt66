"""Executed by the fixed SIEM reader inside kt66-siem; QUERY is validated by the caller."""
import base64
import datetime
import gzip
import json
import re
from pathlib import Path
import time


def search(query, directory=Path('/var/ossec/logs/alerts')):
    start, end = query['start'], query['end']
    paths = [directory / 'alerts.json']
    # Wazuh stores historical alerts in YYYY/Mon/ossec-alerts-DD.json[.gz].
    first = datetime.datetime.fromtimestamp(start, datetime.timezone.utc).date() - datetime.timedelta(days=1)
    last = datetime.datetime.fromtimestamp(end, datetime.timezone.utc).date() + datetime.timedelta(days=1)
    day = first
    while day <= last:
        stem = directory / day.strftime('%Y/%b/ossec-alerts-%d.json')
        paths.append(stem if stem.is_file() else Path(str(stem) + '.gz'))
        day += datetime.timedelta(days=1)
    paths = sorted({p for p in paths if p.is_file()}, key=str)
    position, offset = 0, 0
    signature = str(start) + ':' + str(end)
    if query.get('cursor'):
        cur = json.loads(base64.urlsafe_b64decode(query['cursor']).decode())
        if cur['range'] != signature or cur['file'] not in [str(p.relative_to(directory)) for p in paths]:
            raise ValueError('검색 파일 또는 범위가 변경됐습니다. 처음부터 다시 조회하세요')
        position = [str(p.relative_to(directory)) for p in paths].index(cur['file'])
        offset = int(cur['offset'])
        if not 0 <= offset <= 2 * 1024**3:
            raise ValueError('invalid offset')
    deadline, read_bytes = time.monotonic() + 20, 0
    records, files, issues = [], [], []
    next_cursor = None
    for index in range(position, len(paths)):
        path = paths[index]
        files.append(str(path))
        try:
            with (gzip.open(path, 'rb') if path.suffix == '.gz' else path.open('rb')) as source:
                source.seek(offset if index == position else 0)
                while True:
                    marker = source.tell()
                    if len(records) >= query['limit'] or read_bytes >= 64 * 1024 * 1024 or time.monotonic() >= deadline:
                        next_cursor = base64.urlsafe_b64encode(json.dumps(dict(range=signature, file=str(path.relative_to(directory)), offset=marker)).encode()).decode()
                        break
                    line = source.readline(2 * 1024 * 1024)
                    if not line:
                        break
                    read_bytes += len(line)
                    try:
                        row = json.loads(line)
                        raw = row.get('timestamp', '').replace('Z', '+00:00')
                        raw = re.sub(r'([+-]\d{2})(\d{2})$', r'\1:\2', raw)
                        stamp = datetime.datetime.fromisoformat(raw).timestamp()
                        if start <= stamp <= end:
                            records.append({**{k: row[k] for k in ('id', 'timestamp', 'rule', 'agent', 'data', 'location', 'full_log') if k in row},
                                            '_source': str(path), '_offset': marker})
                    except (ValueError, TypeError):
                        if len(issues) < 20:
                            issues.append({'file': str(path), 'offset': marker, 'error': 'invalid_record'})
        except OSError:
            issues.append({'file': str(path), 'error': 'unreadable'})
        if next_cursor:
            break
    return dict(records=records, next_cursor=next_cursor, scanned_sources=files,
                available_sources=[str(p) for p in paths], page_count=len(records), issues=issues,
                retained_sources_exhausted=next_cursor is None,
                coverage='partial' if issues or next_cursor or not paths else 'retained_alert_files',
                limitation='보관된 경보 파일 검색입니다. 파일이 없는 기간 또는 수집되지 않은 원본 이벤트의 완전성을 보장하지 않습니다.')


if __name__ == '__main__':
    print(json.dumps(search(QUERY), ensure_ascii=False))
