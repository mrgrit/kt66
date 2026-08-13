#!/bin/bash
# kt66-net.sh — 호스트레벨 네트워크 글루 (멱등).
#
# `docker compose up` 만으로는 fw→ips→web 인터-브리지 체인이 동작하지 않는다. 세 가지가 필요:
#   1) net.bridge.bridge-nf-call-iptables=0  — br_netfilter 가 docker 브리지 통과 패킷을
#      host iptables 로 넘기면 (a) docker per-IP DROP 에 걸려 체인이 끊기고,
#      (b) docker 의 per-network MASQUERADE 가 fw 의 DNAT 뒷다리를 잡는다. (아래 ★)
#   2) DOCKER-USER 에 브리지 간 ACCEPT — docker 는 다른 브리지 간 forward 를 기본 차단.
#   3) raw PREROUTING accept — Docker 28+ 안티-스푸핑 우회.
#
# ★ 1) 을 빠뜨렸을 때 실제로 일어나는 일 (2026-08-13 실측)
#   호스트에는 docker 가 심은 룰이 있다:
#       -A POSTROUTING -s 10.20.30.0/24 ! -o <br-ext> -j MASQUERADE
#   공격자(.202)가 web 에 **직접** 가는 흐름은 첫 패킷이 br-ext 로 나가므로 이 룰을 비껴가고,
#   그 conntrack 항목이 남아 뒷다리도 그대로 통과한다. 그래서 직접 접근은 멀쩡히 된다.
#   그런데 fw 가 DNAT 하면(10.20.30.1:8002 → 10.20.32.80:8002) **5-튜플이 바뀌어 새 흐름**이
#   되고, 그 첫 패킷은 br-pipe 로 나가므로 위 룰에 걸려 출처가 10.20.31.254(호스트)로
#   바뀐다. web 의 응답은 호스트로 돌아가고 거기서 끊긴다.
#   증상: 게시 포트(호스트 8001~8007)와 fw 경유 접근이 **전부 타임아웃**. 직접 접근만 됨.
#   즉 "출처 IP 보존"이 아니라 아예 **입구가 죽는다.** 조용히.
#
#   더 나쁜 것은 이 sysctl 이 **docker 데몬이 뜰 때마다 1 로 되돌아간다**는 점이다.
#   /etc/sysctl.conf 에 적어 두어도 docker 가 나중에 덮는다. 그래서 compose 의
#   netglue 서비스가 매 기동마다 이걸 다시 0 으로 만든다(kt66.sh up 이 부르는 것과 같은 파일).
#
# 출처 IP 보존(.202 → web) 은 daemon.json 의 "userland-proxy": false 와 함께 성립한다.
# (이 스크립트는 sysctl/iptables 만 — daemon.json 은 setup 시 1회 설정.)
#
# 사용:
#   ./kt66-net.sh          적용(멱등)
#   ./kt66-net.sh --check  진단만. 어긋나 있으면 종료코드 1 — 관제 화면이 이걸 본다
set -e
MODE="${1:-apply}"
SUDO() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi; }
# 진단용 — 비밀번호를 묻지 않는다(-n). root 면 sudo 자체를 거치지 않으므로
# -n 을 붙이면 안 된다. 붙였다가 root 로 돌려도 "권한 없음"이 떴다.
SUDO_Q() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo -n "$@" 2>/dev/null; fi; }

# ── 브리지 이름 찾기 ────────────────────────────────────────────────
# docker CLI 가 있으면 그것으로, 없으면(=netglue 컨테이너 안) 호스트 인터페이스의
# 게이트웨이 주소로 찾는다. kt66 은 각 망의 .254 를 호스트가 갖는다.
declare -A BR
declare -A NETIP=( [ext]=10.20.30.254 [pipe]=10.20.31.254 [dmz]=10.20.32.254 [int]=10.20.40.254 )
for n in ext pipe dmz int; do
    br=""
    if command -v docker >/dev/null 2>&1; then
        id=$(docker network inspect "kt66-$n" -f '{{.Id}}' 2>/dev/null | cut -c1-12) || true
        br=$(docker network inspect "kt66-$n" -f '{{range $k,$v := .Options}}{{if eq $k "com.docker.network.bridge.name"}}{{$v}}{{end}}{{end}}' 2>/dev/null) || true
        [ -z "$br" ] && [ -n "$id" ] && br="br-$id"
    fi
    # 폴백: 그 망의 호스트 주소를 가진 인터페이스가 곧 브리지다
    if [ -z "$br" ]; then
        br=$(ip -4 -o addr show | awk -v a="${NETIP[$n]}/24" '$4==a{print $2; exit}')
    fi
    BR[$n]="$br"
done
if [ -z "${BR[pipe]}" ] || [ -z "${BR[dmz]}" ]; then
    echo "[kt66-net] ERROR: kt66 브리지 미탐지 — 'docker compose up' 먼저 실행"; exit 1
fi

# ── 진단 모드 ───────────────────────────────────────────────────────
if [ "$MODE" = "--check" ]; then
    rc=0
    v=$(cat /proc/sys/net/bridge/bridge-nf-call-iptables 2>/dev/null || echo "?")
    if [ "$v" != "0" ]; then
        echo "[kt66-net] ✗ bridge-nf-call-iptables=$v (0 이어야 한다) — fw 입구가 죽어 있다"; rc=1
    else
        echo "[kt66-net] ✓ bridge-nf-call-iptables=0"
    fi
    # iptables/nft 조회는 root 가 필요하다. 권한이 없으면 **'없다'가 아니라 '못 봤다'**로
    # 보고한다. 처음엔 조회 실패를 그냥 없음으로 처리했는데, 룰이 멀쩡히 걸려 있는데도
    # ✗ 가 떴다. 못 본 것을 없다고 말하는 진단은 진단이 아니라 오보다.
    _rules=""; _seen=0
    if _rules="$(SUDO_Q iptables -S DOCKER-USER)" && [ -n "$_rules" ]; then
        _seen=1
        if printf '%s' "$_rules" | grep -q -- "-i ${BR[pipe]} -o ${BR[dmz]}"; then
            echo "[kt66-net] ✓ DOCKER-USER 인터-브리지 ACCEPT"
        else
            echo "[kt66-net] ✗ DOCKER-USER 인터-브리지 ACCEPT 없음"; rc=1
        fi
    else
        echo "[kt66-net] ? DOCKER-USER — 권한이 없어 확인 못 함 (sudo $0 --check)"; rc=2
    fi
    if [ "$_seen" = 1 ]; then
        if SUDO_Q nft list chain ip raw PREROUTING | grep -q "kt66-interbridge"; then
            echo "[kt66-net] ✓ raw PREROUTING 우회룰"
        else
            echo "[kt66-net] ✗ raw PREROUTING 우회룰 없음"; rc=1
        fi
    else
        echo "[kt66-net] ? raw PREROUTING — 권한이 없어 확인 못 함"
    fi
    # 0 정상 · 1 어긋남 · 2 판정 불가. 셋을 구분해야 호출부가 옳게 처리한다.
    exit $rc
fi

echo "[kt66-net] 1) bridge-nf-call-iptables=0 (인터-브리지 forward 허용 + DNAT 뒷다리 masquerade 방지)"
SUDO sysctl -w net.bridge.bridge-nf-call-iptables=0 >/dev/null
grep -q 'bridge-nf-call-iptables' /etc/sysctl.conf 2>/dev/null || \
    echo 'net.bridge.bridge-nf-call-iptables=0' | SUDO tee -a /etc/sysctl.conf >/dev/null

echo "[kt66-net] 2) 브리지"
for n in ext pipe dmz int; do echo "    kt66-$n -> ${BR[$n]}"; done

echo "[kt66-net] 3) DOCKER-USER ACCEPT (ext<->pipe<->dmz<->int)"
SUDO iptables -F DOCKER-USER 2>/dev/null || true
for pair in \
    "${BR[ext]} ${BR[pipe]}" "${BR[pipe]} ${BR[ext]}" \
    "${BR[pipe]} ${BR[dmz]}" "${BR[dmz]} ${BR[pipe]}" \
    "${BR[dmz]} ${BR[int]}"  "${BR[int]} ${BR[dmz]}"  ; do
    set -- $pair
    SUDO iptables -I DOCKER-USER -i "$1" -o "$2" -j ACCEPT 2>/dev/null || true
done
SUDO iptables -A DOCKER-USER -j RETURN 2>/dev/null || true

echo "[kt66-net] 4) raw PREROUTING accept (Docker 28+ 안티-스푸핑 우회 — 인터-브리지 라우팅)"
# Docker 28+ 는 'table ip raw' PREROUTING 에 컨테이너별 안티-스푸핑 룰을 심는다:
#   iifname != <컨테이너 브리지> ip daddr <컨테이너IP> drop
# 이는 conntrack/forward(DOCKER-USER) 훅보다 *먼저*(raw 단계) 실행되므로, 위의
# DOCKER-USER ACCEPT 만으로는 인터-브리지 라우팅이 살지 않는다(fw↔siem 등 비대칭
# 리턴 패킷이 raw 에서 drop). kt66 4개 브리지 간 lab 트래픽(10.20/16↔10.20/16)을
# raw 최상단에서 먼저 accept 하여 우회한다. 실제 격리/필터링은 DOCKER-USER 가 담당.
if command -v nft >/dev/null 2>&1; then
    for h in $(SUDO nft -a list chain ip raw PREROUTING 2>/dev/null | awk '/comment "kt66-interbridge"/{print $NF}'); do
        SUDO nft delete rule ip raw PREROUTING handle "$h" 2>/dev/null || true
    done
    BRSET="{ \"${BR[ext]}\", \"${BR[pipe]}\", \"${BR[dmz]}\", \"${BR[int]}\" }"
    if SUDO nft insert rule ip raw PREROUTING iifname "$BRSET" \
        ip saddr 10.20.0.0/16 ip daddr 10.20.0.0/16 counter accept \
        comment "kt66-interbridge" 2>/dev/null; then
        echo "    raw PREROUTING accept 적용 (10.20/16 인터-브리지 → 안티-스푸핑 우회)"
    else
        echo "    WARN: raw PREROUTING accept 실패 (nft 미지원?) — 인터-브리지 라우팅 끊길 수 있음"
    fi
fi

echo "[kt66-net] 완료 — fw→ips→web 체인 + 출처 IP 보존 활성."
