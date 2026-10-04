#!/bin/bash
set -euo pipefail
# 게스트와 설치기의 데이터 트래픽은 IPS → FW로 보낸다.
: "${PASSWORD:?Windows 전용 비밀번호를 .env에 설정하세요}"
ip route replace default via 10.20.70.1
# 호스트가 게시한 인증된 웹 콘솔 응답만 관리 경로로 반환한다.
# 게스트 전달 트래픽에는 이 로컬 출력 정책을 적용하지 않는다.
ip route replace default via 10.20.70.254 table 166
ip rule add priority 166 iif lo ipproto tcp sport 8006 lookup 166
# Docker의 internal 네트워크 DNS 프록시는 외부 질의를 전달하지 않는다.
# DNS도 IPS/FW의 제한된 53번 포트 경로를 사용한다.
printf 'nameserver 10.20.32.53\n' > /etc/resolv.conf
exec /usr/bin/tini -s /run/entry.sh
