#!/bin/sh
set -eu
# Compose 파일 secret의 호스트 0600은 PHP(www-data)가 읽을 수 없으므로 내부에만 복사한다.
install -m 640 -o www-data -g www-data /run/secrets/kt66_roundcube_seed /tmp/kt66-roundcube-key
exec /docker-entrypoint.sh "$@"
