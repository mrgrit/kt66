#!/bin/sh
# kt66 gpu-gw 기동 — app 존 ↔ DGX Spark 터널.
#
# 주소 배치
#   eth0(app bridge)  10.20.50.2/24   — 랩 쪽 다리
#   wg0               10.20.50.3/32   — 터널 종단
#   peer(DGX Spark)   10.20.50.10/32  — 3F 존의 정식 구성원으로 보인다
#
# 랩→DGX 경로는 ips 가 10.20.50.10/32 를 이쪽으로 보내면서 성립한다(/32 가 연결된 /24 보다
# longest-prefix 로 우선). 그래서 존 밖에서 오는 트래픽은 반드시 ips 를 지난다.
set -e

IPS_APP_IP="${IPS_APP_IP:-10.20.50.1}"
DOCKER_GW="${DOCKER_GW:-10.20.50.254}"   # app 브리지의 docker 게이트웨이(=호스트)
LAB_CIDR="${LAB_CIDR:-10.20.0.0/16}"
WG_CONF="${WG_CONF:-/etc/wireguard/wg0.conf}"

echo "[gpu-gw] 인터페이스"
ip -o -4 addr show | grep -v ' lo ' | awk '{print "     " $2 "  " $4}'

# 랩 안쪽으로 돌아가는 길은 전부 ips 를 지난다. 기본 경로를 ips 로 둔다 —
# docker 브리지 게이트웨이(.254=호스트)로 나가면 보안장비를 건너뛰게 된다.
echo "[gpu-gw] 기본 경로 → ips ($IPS_APP_IP)"
ip route replace default via "$IPS_APP_IP" 2>/dev/null || \
    echo "[gpu-gw] WARN: 기본 경로 설정 실패 — ips 가 app 존에 올라왔는지 확인"

# 터널의 *바깥* 패킷은 인프라 트래픽이다 — 랩 보안장비를 지날 이유가 없고, ips 로 보내면
# 호스트의 masquerade 를 못 받아 출발지가 10.20.50.2 인 채로 인터넷에 나간다(상대가 회신을
# 못 보낸다). 그래서 WG 엔드포인트로 가는 길만 docker 게이트웨이로 따로 뺀다.
# 터널 *안쪽*(복호화된 랩 트래픽)은 위의 기본 경로를 그대로 타므로 여전히 ips 를 지난다.
WG_ENDPOINT_HOST=$(awk -F'[ =:]+' '/^ *Endpoint/ {print $2; exit}' "$WG_CONF" 2>/dev/null)
if [ -n "$WG_ENDPOINT_HOST" ]; then
    WG_ENDPOINT_IP=$(getent hosts "$WG_ENDPOINT_HOST" 2>/dev/null | awk '{print $1; exit}')
    [ -z "$WG_ENDPOINT_IP" ] && WG_ENDPOINT_IP="$WG_ENDPOINT_HOST"
    ip route replace "$WG_ENDPOINT_IP/32" via "$DOCKER_GW" 2>/dev/null && \
        echo "[gpu-gw] 터널 외부 경로: $WG_ENDPOINT_IP -> docker gw($DOCKER_GW), 랩 체인 우회" || \
        echo "[gpu-gw] WARN: 터널 외부 경로 설정 실패 — 핸드셰이크가 안 될 수 있다"
fi

if [ ! -f "$WG_CONF" ]; then
    echo "[gpu-gw] ERROR: $WG_CONF 없음. gpu-gw/wg/ 에 키·설정을 먼저 만든다:"
    echo "           ./gpu-gw/setup-tunnel.sh"
    exec tail -f /dev/null
fi

echo "[gpu-gw] WireGuard 기동"
wg-quick up "$WG_CONF" 2>&1 | sed 's/^/     /'

echo "[gpu-gw] 터널 상태"
wg show | sed 's/^/     /'

echo "[gpu-gw] 라우팅"
ip route | sed 's/^/     /'

echo "[gpu-gw] 가동 — 3F GPU 존 게이트웨이"
exec tail -f /dev/null
