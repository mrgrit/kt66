#!/bin/sh
# kt66 환경 시뮬레이터 기동.
#
# envsim 은 두 다리를 갖는다: ot(10.20.60.10, 시설망)와 dmz(10.20.32.60, SIEM 으로 가는 다리).
# docker 가 준 기본 경로는 dmz 브리지 게이트웨이(=호스트)다. 그런데 3F GPU 존의
# DGX Spark(10.20.50.10)는 터널 너머라 **호스트는 그 주소를 모른다** — ips 만 안다.
#
# 그래서 app 존으로 가는 경로를 ot 쪽 게이트웨이(ips)로 명시한다. 우회로를 뚫는 게
# 아니라 오히려 반대다: 시설망에서 GPU 존으로 가는 트래픽도 ips 를 지나게 만든다.
# (ips 의 kt66ot 테이블이 ot 발신을 허용하고, 회신은 established 로 돌아온다.)
set -e

OT_GW="${OT_GW:-10.20.60.1}"        # ips 의 ot 다리
APP_CIDR="${APP_CIDR:-10.20.50.0/24}"

if ip route replace "$APP_CIDR" via "$OT_GW" 2>/dev/null; then
    echo "[envsim] GPU 존 경로: $APP_CIDR via ips($OT_GW)"
else
    echo "[envsim] GPU 존 경로 설정 실패 — GPU 사용률은 0 으로 잡힌다(치명적이지 않음)"
fi

exec uvicorn app:app --host 0.0.0.0 --port 8000
