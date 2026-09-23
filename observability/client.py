"""SIEM 연결. TLS 검증과 계정 범위를 유지하며 오류에 인증 정보를 싣지 않는다."""
import base64
import json
import os
import ssl
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPSHandler, ProxyHandler


class IndexErrorResponse(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(f'SIEM 응답 오류 ({status})')


class IndexClient:
    def __init__(self, credentials=None, certificate=None):
        self.url = os.environ.get('OBS_INDEXER_URL', 'https://wazuh.indexer:9200').rstrip('/')
        if not self.url.startswith('https://'):
            raise ValueError('SIEM 연결은 검증된 HTTPS를 사용해야 합니다')
        context = ssl.create_default_context(cafile=os.environ.get('OBS_CA', '/certs/root-ca.pem'))
        self.headers = {}
        if certificate:
            context.load_cert_chain(*certificate)
        else:
            login = json.loads(Path(credentials or os.environ.get('OBS_READER_CREDENTIALS', '/siem-reader/account.json')).read_text())
            token = base64.b64encode((login['username'] + ':' + login['password']).encode()).decode()
            self.headers['Authorization'] = 'Basic ' + token
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context))

    def request(self, method, path, data=None):
        binary = isinstance(data, bytes)
        body = data if binary else json.dumps(data).encode() if data is not None else None
        request = Request(self.url + path, data=body, method=method,
                          headers={**self.headers, 'Content-Type': 'application/x-ndjson' if binary else 'application/json'})
        try:
            with self.opener.open(request, timeout=12) as response:
                return json.loads(response.read(32_000_000))
        except HTTPError as error:
            raise IndexErrorResponse(error.code) from None
        except (URLError, TimeoutError, OSError):
            raise IndexErrorResponse('연결 실패') from None

    def search(self, index, query):
        # 호출자는 고정 인덱스와 구조화된 쿼리를 사용한다. 외부 경로/DSL 프록시가 아니다.
        result = self.request('POST', '/' + index + '/_search?allow_no_indices=true&ignore_unavailable=true', query)
        if result.get('timed_out') or result.get('_shards', {}).get('failed', 0):
            raise IndexErrorResponse('불완전 검색')
        return result
