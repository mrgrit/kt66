#!/usr/bin/env python3
"""Microsoft 공식 한국어 Windows 11 Enterprise LTSC 평가판만 내려받는다."""
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request

SOURCE = 'https://www.microsoft.com/en-us/evalcenter/download-windows-11-enterprise'
URL = ('https://software-static.download.prss.microsoft.com/dbazure/888969d5-f34g-4e03-ac9d-1f9786c66749/'
       '26100.1742.240906-0331.ge_release_svc_refresh_CLIENT_LTSC_EVAL_x64FRE_ko-kr.iso')
MEDIA = Path(__file__).resolve().parent / 'media'
ISO = MEDIA / 'windows-eval.iso'


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    MEDIA.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(URL, method='HEAD'), timeout=30) as response:
        size = int(response.headers['Content-Length'])
    try:
        saved = json.loads((MEDIA / 'source.json').read_text())
        reusable = (saved.get('url') == URL and saved.get('bytes') == size and ISO.stat().st_size == size
                    and saved.get('sha256') == checksum(ISO))
    except (OSError, ValueError):
        reusable = False
    if not reusable:
        partial = ISO.with_suffix('.part')
        subprocess.run(['curl', '--noproxy', '*', '--fail', '--location', '--show-error', '--retry', '2',
                        '--connect-timeout', '20', '--proto', '=https', '--proto-redir', '=https',
                        '--continue-at', '-', '--output', str(partial), URL], check=True)
        if partial.stat().st_size != size:
            raise SystemExit('공식 서버의 파일 크기와 달라 설치를 중단합니다.')
        partial.replace(ISO)
    record = {'source': SOURCE, 'url': URL, 'bytes': size, 'sha256': checksum(ISO),
              'hash_basis': '공식 HTTPS 다운로드의 로컬 SHA-256; Microsoft 게시 해시 대조와는 별개'}
    (MEDIA / 'source.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print('공식 평가판 ISO 준비 완료. 출처·크기·SHA-256: endpoints/windows/media/source.json')


if __name__ == '__main__':
    main()
