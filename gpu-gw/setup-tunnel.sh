#!/bin/bash
# setup-tunnel.sh — kt66 랩 ↔ GPU 서버 WireGuard 터널 설정 (멱등).
#
# 키를 만들고 양쪽 설정 파일을 생성한다. 랩 쪽(wg0.conf)은 gpu-gw 컨테이너가 마운트하고,
# GPU 쪽(dgx-wg0.conf)은 GPU 서버에 복사해 넣는다.
#
#   ./setup-tunnel.sh <GPU서버_공인IP> [SSH계정]
#
# 방향이 중요하다. 랩 호스트는 NAT 뒤라 인바운드를 못 받고, GPU 서버는 공인 IP 다.
# 그래서 **GPU 서버가 듣고(ListenPort), 랩이 건다(Endpoint + PersistentKeepalive)**.
set -e
SELFDIR="$(dirname "$(readlink -f "$0")")"
WG="$SELFDIR/wg"

GPU_HOST="${1:?사용법: ./setup-tunnel.sh <GPU서버_공인IP> [SSH계정]}"
GPU_USER="${2:-mrgrit}"
GPU_PORT="${GPU_PORT:-51820}"

# 주소 배치 — README 의 3F GPU 존과 일치해야 한다
LAB_WG_IP="10.20.50.3"       # gpu-gw 의 터널 종단
DGX_LAB_IP="10.20.50.10"     # DGX Spark 가 3F 존에서 갖는 주소
LAB_CIDR="10.20.0.0/16"

mkdir -p "$WG"
chmod 700 "$WG"

gen() {  # $1 = 이름
    if [ ! -f "$WG/$1.key" ]; then
        wg genkey > "$WG/$1.key"
        chmod 600 "$WG/$1.key"
        wg pubkey < "$WG/$1.key" > "$WG/$1.pub"
        echo "[tunnel] 키 생성: $1"
    else
        echo "[tunnel] 키 이미 존재: $1 — 재사용"
    fi
}

command -v wg >/dev/null || {
    echo "[tunnel] wg 도구가 없다. 컨테이너로 대신 생성한다."
    wg() { docker run --rm alpine:3.20 sh -c "apk add -q --no-cache wireguard-tools && wg $*"; }
}

gen lab
gen dgx
LAB_PRIV=$(cat "$WG/lab.key");  LAB_PUB=$(cat "$WG/lab.pub")
DGX_PRIV=$(cat "$WG/dgx.key");  DGX_PUB=$(cat "$WG/dgx.pub")

# ── 랩 쪽 (gpu-gw 컨테이너가 마운트) ────────────────────────────────
# NAT 뒤이므로 이쪽이 먼저 건다. PersistentKeepalive 로 NAT 매핑을 살려 둔다.
cat > "$WG/wg0.conf" <<CONF
# kt66 gpu-gw — 랩 쪽 터널 종단. setup-tunnel.sh 가 생성한다(직접 편집 금지).
[Interface]
PrivateKey = $LAB_PRIV
Address    = $LAB_WG_IP/32

[Peer]
# DGX Spark — 3F GPU 존의 $DGX_LAB_IP
PublicKey           = $DGX_PUB
Endpoint            = $GPU_HOST:$GPU_PORT
AllowedIPs          = $DGX_LAB_IP/32
PersistentKeepalive = 25
CONF
chmod 600 "$WG/wg0.conf"

# ── GPU 서버 쪽 ─────────────────────────────────────────────────────
# 공인 IP 라 듣기만 하면 된다. Endpoint 를 적지 않는다(랩 주소가 NAT 로 바뀔 수 있다).
# AllowedIPs 를 랩 대역 전체로 두어 랩으로 가는 트래픽만 터널을 타게 한다 —
# **기본 경로는 건드리지 않는다.** GPU 서버의 인터넷은 그대로 자기 회선을 쓴다.
cat > "$WG/dgx-wg0.conf" <<CONF
# kt66 — GPU 서버(DGX Spark) 쪽 터널 종단. 3F GPU 존 $DGX_LAB_IP.
# 랩 대역만 터널로 보낸다. 기본 경로(인터넷)는 이 서버의 원래 회선을 그대로 쓴다.
[Interface]
PrivateKey = $DGX_PRIV
Address    = $DGX_LAB_IP/32
ListenPort = $GPU_PORT

[Peer]
# kt66 랩 (NAT 뒤 — Endpoint 없음. 랩이 먼저 건다)
PublicKey  = $LAB_PUB
AllowedIPs = $LAB_CIDR
CONF
chmod 600 "$WG/dgx-wg0.conf"

echo
echo "[tunnel] 생성 완료"
echo "  랩  : $WG/wg0.conf      → gpu-gw 컨테이너가 마운트 ($LAB_WG_IP)"
echo "  GPU : $WG/dgx-wg0.conf  → GPU 서버로 복사 ($DGX_LAB_IP)"
echo
echo "GPU 서버 적용:"
echo "  scp $WG/dgx-wg0.conf $GPU_USER@$GPU_HOST:/tmp/wg0.conf"
echo "  ssh $GPU_USER@$GPU_HOST 'sudo apt-get install -y wireguard-tools &&"
echo "      sudo install -m600 /tmp/wg0.conf /etc/wireguard/wg0.conf && rm /tmp/wg0.conf &&"
echo "      sudo systemctl enable --now wg-quick@wg0'"
