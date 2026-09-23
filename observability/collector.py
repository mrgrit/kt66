"""증적 → SQLite 전송함 → SIEM. ACK 전에는 전송함에서 지우지 않으며 동일 ID로 재전송한다."""
import json
import os
from pathlib import Path
import sqlite3
import time
import yaml
from activity_audit import SECRET_KEY
from client import IndexClient
from projection import RUN, document, finding_document, control_document, safe_document, digest, iso


class Collector:
    def __init__(self, root, state, client):
        self.root, self.state, self.client = Path(root).resolve(), Path(state), client
        self.state.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.state / 'outbox.sqlite3')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, signature TEXT, offset INTEGER, line INTEGER);
            CREATE TABLE IF NOT EXISTS outbox(key TEXT PRIMARY KEY, idx TEXT, id TEXT, body TEXT, error TEXT);
            CREATE TABLE IF NOT EXISTS records(key TEXT PRIMARY KEY, digest TEXT);
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT);
        ''')
        if self.get('projection_version') != 2:
            # 변환 규약이 바뀌면 원문을 다시 투영한다. 전송 대기열/기존 SIEM 문서는 지우지 않는다.
            with self.db:
                self.db.execute('DELETE FROM files')
                self.set('projection_version', 2)
        self.secrets = [v for k, v in os.environ.items() if SECRET_KEY.search(k)]
        self.secrets += [json.loads(Path(os.environ.get('OBS_WRITER_CREDENTIALS', '/siem-writer/account.json')).read_text()).get('password', '')] if os.environ.get('OBS_WRITER_CREDENTIALS') else []
        self.stats = {'started_at': time.time(), 'source_errors': [], 'last_scan': None, 'last_success': self.get('last_success'), 'exported': self.get('exported') or 0}

    def get(self, key):
        row = self.db.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', (key, json.dumps(value)))

    def valid(self, path):
        return not path.is_symlink() and path.resolve().is_relative_to(self.root)

    def read(self, path, limit=4_000_000):
        if not self.valid(path) or path.stat().st_size > limit:
            raise ValueError('증적 경로/크기 범위 초과')
        return json.loads(path.read_text())

    def enqueue(self, item):
        if item is None:
            return
        index, identifier, doc = item
        doc = safe_document(doc, self.secrets)
        fingerprint = digest(json.dumps(doc, sort_keys=True, ensure_ascii=False))
        key = index + ':' + identifier
        previous = self.db.execute('SELECT digest FROM records WHERE key=?', (key,)).fetchone()
        if previous and previous[0] == fingerprint:
            return
        doc['ingested_at'] = iso(time.time())
        self.db.execute('INSERT OR REPLACE INTO outbox VALUES (?,?,?,?,NULL)', (key, index, identifier, json.dumps(doc, ensure_ascii=False)))
        self.db.execute('INSERT OR REPLACE INTO records VALUES (?,?)', (key, fingerprint))

    def meta(self, directory):
        job = self.read(directory / 'job.json') if (directory / 'job.json').exists() else {}
        manifest = self.read(directory / 'manifest-snapshot.json') if (directory / 'manifest-snapshot.json').exists() else {}
        worker = manifest.get('worker') or {}
        if not isinstance(worker, dict):
            worker = {}
        pieces = directory.name.split('-', 2)
        try:
            started = int(pieces[1]) / 1e9
            if not 1_000_000_000 < started < 32_503_680_000:
                raise ValueError()
        except (ValueError, IndexError):
            started = directory.stat().st_mtime
        return {'worker': worker.get('id') or job.get('worker') or (pieces[2] if len(pieces) == 3 else 'unknown'),
                'role': worker.get('security_role'), 'run_id': directory.name, 'trigger': job.get('kind'), 'started_at': started}

    def consume(self, path, meta, cfg):
        if not self.valid(path):
            raise ValueError('심볼릭 링크·경로 탈출 거부')
        relative, st = path.relative_to(self.root).as_posix(), path.stat()
        signature = f'{st.st_ino}:{st.st_mtime_ns}:{st.st_size}'
        row = self.db.execute('SELECT signature,offset,line FROM files WHERE path=?', (relative,)).fetchone()
        if row and row[0] == signature:
            return False
        offset, line = (row[1], row[2]) if row and row[0].split(':')[0] == str(st.st_ino) and st.st_size >= row[1] else (0, 0)
        complete = True
        if path.name in ('tools.jsonl', 'activity.jsonl'):
            with path.open('rb') as stream:
                stream.seek(offset)
                for _ in range(500):
                    raw = stream.readline(8_000_001)
                    if not raw:
                        break
                    if len(raw) > 8_000_000:
                        raise ValueError('단일 증적 8MB 상한 초과 · 원문 유지')
                    if not raw.endswith(b'\n'):
                        complete = False
                        break  # 쓰기 중인 줄은 다음 주기에 다시 읽는다.
                    try:
                        record = json.loads(raw)
                        if not isinstance(record, dict):
                            raise ValueError('객체 형식 아님')
                    except (ValueError, UnicodeError):
                        # 손상 줄을 묵살하거나 건너뛰지 않는다. 복구 전까지 이 파일의 커서는 정지.
                        raise ValueError(f'JSON 증적 손상 · {line + 1}행') from None
                    line += 1
                    self.enqueue(document(relative, record, meta, st.st_mtime, cfg, line, source_bytes=raw))
                    offset = stream.tell()
                complete = complete and offset == st.st_size
        else:
            if path.suffix == '.md':
                if st.st_size > 400_000:
                    raise ValueError('보고서 크기 상한 초과')
                raw = path.read_text()
            else:
                raw = self.read(path)
            self.enqueue(document(relative, raw, meta, st.st_mtime, cfg, source_bytes=path.read_bytes()))
            offset, line = st.st_size, 0
        # 중간 배치/쓰는 중인 파일은 완료 서명을 저장하지 않는다.
        stored_signature = signature if complete else f'{st.st_ino}:partial:{st.st_size}'
        self.db.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?)', (relative, stored_signature, offset, line))
        return True

    def scan(self):
        errors, changed = [], 0
        config_path = self.root / 'xoc/risk-zones.yaml'
        cfg = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
        directories = sorted((p for p in (self.root / 'evidence').iterdir() if p.is_dir() and self.valid(p) and RUN.fullmatch(p.name)), reverse=True)
        for directory in directories:
            if changed >= 600:
                break
            try:
                # 결과의 정본만 처리하여 중간 session-result로 최종 결과를 덮지 않는다.
                result = next((directory / n for n in ('result.json', 'session-result.json', 'failure.json') if (directory / n).exists()), None)
                paths = [directory / 'tools.jsonl', directory / 'activity.jsonl', *sorted(directory.glob('finding-*.md'))]
                if result:
                    paths.append(result)
                metadata = None
                for path in paths:
                    if not path.exists():
                        continue
                    st = path.stat()
                    old = self.db.execute('SELECT signature FROM files WHERE path=?', (path.relative_to(self.root).as_posix(),)).fetchone()
                    if old and old[0] == f'{st.st_ino}:{st.st_mtime_ns}:{st.st_size}':
                        continue
                    if metadata is None:
                        metadata = self.meta(directory)
                    with self.db:
                        changed += self.consume(path, metadata, cfg)
            except (OSError, ValueError, TypeError) as error:
                errors.append({'source': directory.name, 'error': str(error)[:120]})
        for path in (self.root / 'tickets').glob('*.md'):
            try:
                with self.db:
                    self.consume(path, {'worker': 'unknown', 'run_id': ''}, cfg)
            except (OSError, ValueError) as error:
                errors.append({'source': path.name, 'error': str(error)[:120]})
        try:
            state = self.read(self.root / 'tickets/xoc/state.json', 50_000_000)
            with self.db:
                for row in state.get('findings', {}).values():
                    self.enqueue(finding_document(row))
                for row in state.get('history', []):
                    self.enqueue(control_document(row))
        except (OSError, ValueError, TypeError, AttributeError):
            errors.append({'source': 'tickets/xoc/state.json', 'error': 'xOC 상태 미수집 · 기존 문서는 유지'})
        self.stats.update(last_scan=time.time(), source_errors=errors[:30], source_error_count=len(errors), scanned_directories=len(directories), scan_limited=changed >= 600)

    def flush(self):
        rows = self.db.execute('SELECT key,idx,id,body FROM outbox ORDER BY rowid LIMIT 400').fetchall()
        if not rows:
            return 0
        payload = ''.join(json.dumps({'index': {'_index': index, '_id': identifier}}) + '\n' + body + '\n' for _, index, identifier, body in rows)
        result = self.client.request('POST', '/_bulk', payload.encode())
        items = result.get('items')
        if not isinstance(items, list) or len(items) != len(rows):
            raise ValueError('SIEM 일괄 응답 건수 불일치 · 재전송 대기')
        accepted = 0
        with self.db:
            for row, item in zip(rows, items):
                response = item.get('index', {})
                if 200 <= response.get('status', 0) < 300:
                    self.db.execute('DELETE FROM outbox WHERE key=?', (row[0],))
                    accepted += 1
                else:
                    self.db.execute('UPDATE outbox SET error=? WHERE key=?', ('SIEM 문서 응답 ' + str(response.get('status')), row[0]))
                    # 오류 문서 하나가 뒤의 모든 정상 문서를 영구 차단하지 않게 뒤로 이동.
                    self.db.execute('UPDATE outbox SET rowid=(SELECT COALESCE(MAX(rowid),0)+1 FROM outbox) WHERE key=?', (row[0],))
            self.stats['exported'] += accepted
            if accepted:
                self.stats['last_success'] = time.time()
                self.set('last_success', self.stats['last_success'])
                self.set('exported', self.stats['exported'])
        return accepted

    def status(self, error=None):
        pending = self.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]
        rejected = self.db.execute('SELECT COUNT(*) FROM outbox WHERE error IS NOT NULL').fetchone()[0]
        result = {**self.stats, 'heartbeat': time.time(), 'pending': pending, 'rejected': rejected, 'error': error,
                  'state': 'degraded' if error or rejected or self.stats.get('source_error_count') else 'backfill' if self.stats.get('scan_limited') else 'running'}
        temp = self.state / 'status.tmp'
        temp.write_text(json.dumps(safe_document(result, self.secrets), ensure_ascii=False))
        temp.replace(self.state / 'status.json')

    def cycle(self):
        error = None
        try:
            if self.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] < 50_000:
                self.scan()
            else:
                self.stats['scan_limited'] = True
            for _ in range(15):
                if not self.flush():
                    break
        except Exception as exc:
            # 내용/URL/인증값을 예외 문자열로 내보내지 않는다.
            error = 'SIEM 전송/수집 실패 · ' + type(exc).__name__
        self.status(error)
        return error


if __name__ == '__main__':
    collector = Collector(os.environ.get('AGENTS_DIR', '/agents'), '/state', IndexClient(credentials=os.environ.get('OBS_WRITER_CREDENTIALS', '/siem-writer/account.json')))
    backoff = 10
    while True:
        failed = collector.cycle()
        backoff = min(120, backoff * 2) if failed else 10
        time.sleep(backoff)
