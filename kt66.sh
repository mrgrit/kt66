#!/usr/bin/env bash
# kt66 — 단일 설치/운영 스크립트.  갓 설치한 Ubuntu → 한 방 배포.
#   sudo ./kt66.sh install     # Docker + daemon.json(userland-proxy=false)
#   ./kt66.sh up               # 인증서·env 생성 → build → core+overlay up → net glue → systemd → sigma
#   ./kt66.sh down             # 전체 내림 (-v 로 볼륨까지)
#   ./kt66.sh net              # 호스트 네트워크 글루만 재적용 (재생성 후)
#   ./kt66.sh certs|env|sigma  # 개별 단계
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

# 웹 진입 publish 바인딩 IP.
#   · 미지정: up 시 VM 실제 IP 자동감지 → .env(WEB_HOST_IP) 기록 (강의실/DHCP 브리지 VM).
#     학생 hosts(kt66.lab→VM_IP) 와 바인딩 IP 가 일치해 VM 밖에서 접속 가능.
#   · 명시 지정(예: WEB_HOST_IP=192.168.0.161 ./kt66.sh up): 그대로 존중 (2-NIC .151 레거시).
WEB_HOST_IP_EXPLICIT=""; [ -n "${WEB_HOST_IP:-}" ] && WEB_HOST_IP_EXPLICIT=1
WEB_HOST_IP="${WEB_HOST_IP:-}"
# 내부 GUI publish 바인딩 IP — 관제(8020)·주입기(8030)·envsim(8010)·agentops(8050)·
# modelops(8060)·infraops(8070)·SIEM(5601)·포털(8000)·콘솔(8081~8083) 이 전부 여기 붙는다.
#   · 예전 기본값은 192.168.136.145 (el34 에서 물려받은 dummy NIC) 였다. 그 서버의
#     브라우저에서만 열리는 주소다 — **새 서버에 배포하면 취약 웹앱·SIEM 은 열리는데
#     데이터센터 콘솔만 통째로 안 열리는** 증상이 여기서 나왔다. 컨테이너는 정상이고
#     포트도 떠 있는데 아무도 닿을 수 없는 IP 에 묶여 있었다.
#   · 이제 기본은 웹 진입 IP 와 같다(= 학생이 이미 쓰는 주소). 배포하면 그냥 열린다.
#   · el34 식 격리(그 호스트 브라우저 전용)를 원하면 .env 에 INT_HOST_IP=192.168.136.145.
INT_HOST_IP_EXPLICIT=""; [ -n "${INT_HOST_IP:-}" ] && INT_HOST_IP_EXPLICIT=1
INT_HOST_IP="${INT_HOST_IP:-}"
SUDO=""; [ "$(id -u)" = 0 ] || SUDO="sudo"
REAL_USER="${SUDO_USER:-$(id -un)}"             # sudo 로 재실행돼도 원래 사용자 (파일 소유 복원용)

# ───────────────────────────────────────────────── helpers
ensure_env() {
    [ -f .env ] || { cp .env.example .env; echo "[kt66] .env 생성(.env.example 복사) — LLM_BASE_URL 등 값 확인 권장"; }
    grep -q '^LLM_MANAGER_MODEL='  .env || echo 'LLM_MANAGER_MODEL=gpt-oss:120b' >> .env
    grep -q '^LLM_SUBAGENT_MODEL=' .env || echo 'LLM_SUBAGENT_MODEL=qwen3:8b'   >> .env
}

detect_primary_ip() {
    # 기본 라우트로 나가는 소스 IP = 브리지/DHCP 로 받은 VM 실제 IP (프롬프트 기본값 제안용)
    ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}'
}

valid_ip() {
    # IPv4 형식 + 각 옥텟 0-255 (0.0.0.0 도 허용 = 모든 인터페이스)
    case "$1" in
        *[!0-9.]*|.*|*.|*..*) return 1 ;;
    esac
    local o1 o2 o3 o4 IFS=.
    read -r o1 o2 o3 o4 <<<"$1"
    [ -n "$o4" ] && for o in "$o1" "$o2" "$o3" "$o4"; do [ "$o" -le 255 ] 2>/dev/null || return 1; done
}

_persist_web_host_ip() {   # $1 = ip. .env 에 기록(멱등).
    if grep -qE '^WEB_HOST_IP=' .env 2>/dev/null; then
        sed -i "s|^WEB_HOST_IP=.*|WEB_HOST_IP=$1|" .env
    else
        printf 'WEB_HOST_IP=%s\n' "$1" >> .env
    fi
}

# 웹 진입 고정 IP 를 확정한다 — 최초 setup(install) 때 사용자에게 1회 물어 .env 에 고정.
#   · 이후 up/재부팅은 .env 값을 그대로 사용(재질문 없음). 변경: WEB_HOST_IP_FORCE=1 ./kt66.sh install
#   · 명시 지정(WEB_HOST_IP=x ./kt66.sh ...)은 프롬프트 없이 그 값으로 고정.
#   · 비대화형(TTY 없음)이면 자동감지값으로 고정.
resolve_web_host_ip() {
    ensure_env   # .env 보장
    local existing; existing="$(grep -E '^WEB_HOST_IP=' .env 2>/dev/null | tail -1 | cut -d= -f2- || true)"

    # 1) 명시 env override — 프롬프트 없이 고정
    if [ -n "$WEB_HOST_IP_EXPLICIT" ]; then
        _persist_web_host_ip "$WEB_HOST_IP"
        echo "[kt66] 웹 진입 IP 고정(명시 지정): ${WEB_HOST_IP} (.env)"; return 0
    fi
    # 2) 이미 고정돼 있고 강제변경 아님 — 그대로 사용(쭉 고정)
    if [ -n "$existing" ] && [ -z "${WEB_HOST_IP_FORCE:-}" ]; then
        WEB_HOST_IP="$existing"
        echo "[kt66] 웹 진입 IP(고정) 사용: ${WEB_HOST_IP} (.env) — 변경: WEB_HOST_IP_FORCE=1 ./kt66.sh install"
        return 0
    fi
    # 3) 최초 지정(또는 강제 변경) — 사용자에게 1회 질의
    local def; def="${existing:-$(detect_primary_ip || true)}"; def="${def:-0.0.0.0}"
    local ans="" ip=""
    if [ -t 0 ]; then
        echo   "[kt66] ── 웹 진입 고정 IP 지정 ──────────────────────────────"
        echo   "  랜딩페이지/취약사이트(kt66.lab)를 이 IP 로 노출하고, 이후 계속 이 값을 씁니다."
        echo   "  · 학생 PC hosts 파일: 'kt66.lab juice.kt66.lab ... → 이 IP' 로 매핑"
        echo   "  · 강의실 DHCP 환경이면 VM 에 이 IP 를 고정(static/DHCP 예약)해 두세요"
        echo   "  · 0.0.0.0 입력 시 모든 인터페이스 바인딩(VM 실제 IP 로 접속)"
        while :; do
            printf "  웹 진입 IP [기본 %s]: " "$def"
            read -r ans || ans=""
            ip="${ans:-$def}"
            if valid_ip "$ip"; then break; fi
            echo "  ✗ '$ip' 는 올바른 IPv4 가 아닙니다. 다시 입력하세요."
        done
    else
        ip="$def"
        echo "[kt66] 비대화형 — 웹 진입 IP 자동감지값으로 고정: ${ip}"
    fi
    WEB_HOST_IP="$ip"
    _persist_web_host_ip "$WEB_HOST_IP"
    echo "[kt66] ✅ 웹 진입 IP 고정: ${WEB_HOST_IP} (.env 기록 — 이후 up/재부팅 모두 이 값)"
}

# 내부 GUI 바인딩 IP 를 확정한다. resolve_web_host_ip 뒤에 부른다(웹 진입 IP 를 물려받으므로).
#   우선순위: 명시 env > .env 기록값 > 웹 진입 IP.
#   묻지 않는다 — 이건 선택지가 아니라 "열리느냐 마느냐"이고, 격리를 원하는 쪽이 .env 에 적는다.
resolve_int_host_ip() {
    local existing; existing="$(grep -E '^INT_HOST_IP=' .env 2>/dev/null | tail -1 | cut -d= -f2- || true)"
    if [ -n "$INT_HOST_IP_EXPLICIT" ]; then
        echo "[kt66] 내부 GUI 바인딩 IP(명시 지정): ${INT_HOST_IP}"
    elif [ -n "$existing" ]; then
        INT_HOST_IP="$existing"
        echo "[kt66] 내부 GUI 바인딩 IP(.env): ${INT_HOST_IP}"
        return 0
    else
        INT_HOST_IP="${WEB_HOST_IP:-0.0.0.0}"
        echo "[kt66] 내부 GUI 바인딩 IP 미설정 → 웹 진입 IP 를 따른다: ${INT_HOST_IP}"
        echo "[kt66]   (호스트 브라우저 전용 격리를 원하면 .env 에 INT_HOST_IP=192.168.136.145)"
    fi
    if grep -qE '^INT_HOST_IP=' .env 2>/dev/null; then
        sed -i "s|^INT_HOST_IP=.*|INT_HOST_IP=$INT_HOST_IP|" .env
    else
        printf 'INT_HOST_IP=%s\n' "$INT_HOST_IP" >> .env
    fi
}

# 구독 이상의 과금이 열릴 수 있는 환경을 짚는다.
#
# kt66 의 프런티어 모델 자리는 토큰당 과금되는 API 가 아니라 **이미 구독 중인 Claude
# Code 세션**이다(agents/cc-session). 그런데 Claude Code 는 환경에 ANTHROPIC_API_KEY
# 같은 변수가 있으면 그쪽을 먼저 쓴다 — 조용히, 아무 경고 없이. 셸 프로파일에 한 줄
# 넣어 둔 사람은 자기가 API 로 붙는지 모른다.
#
# cc-session 은 실행 직전에 그 변수들을 걷어내므로 안전하다. 여기서 짚는 것은 **손으로
# claude 를 띄우는 경우**다. 랩 기동을 막지는 않는다 — kt66 자체는 이 변수를 쓰지 않는다.
check_no_metered_llm() {
    local found=""
    local v
    for v in $(env | sed -n 's/^\(ANTHROPIC_[A-Z_]*\|CLAUDE_CODE_USE_[A-Z_]*\|AWS_BEARER_TOKEN_[A-Z_]*\)=.*/\1/p'); do
        found="$found $v"
    done
    if grep -qE '^[[:space:]]*(ANTHROPIC_|CLAUDE_CODE_USE_)[A-Z_]*[[:space:]]*=[[:space:]]*[^[:space:]]' .env 2>/dev/null; then
        found="$found .env"
    fi
    if [ -n "$found" ]; then
        echo "[kt66] ⚠ 과금 경로가 열려 있다:${found}"
        echo "[kt66]   이 셸에서 손으로 'claude' 를 띄우면 구독이 아니라 토큰당 과금으로 붙는다."
        echo "[kt66]   ./agents/cc-session <근무자> 로 띄우면 실행 직전에 걷어낸다."
    fi
}

# 데이터센터 콘솔이 **바깥에서** 실제로 응답하는지 본다.
# 이 랩에서 가장 조용한 실패가 여기였다: 컨테이너는 Up, 포트도 publish, 그런데
# 바인딩 IP 에 아무도 닿을 수 없어 화면만 안 열린다. up 이 끝날 때 한 번 짚고 넘어간다.
check_datacenter_consoles() {
    local probe="${INT_HOST_IP}"
    [ "$probe" = "0.0.0.0" ] && probe="${WEB_HOST_IP:-127.0.0.1}"
    [ -n "$probe" ] || return 0
    command -v curl >/dev/null || return 0
    local bad="" p
    for p in 8010 8020 8030 8050 8060 8070; do
        local ok=""
        for _ in 1 2 3 4 5 6; do
            # set -e 아래에서는 `cmd && {...}` 가 실패하면 스크립트가 죽는다 — if 로 감싼다.
            if curl -s -o /dev/null --max-time 3 "http://${probe}:${p}/"; then ok=1; break; fi
            sleep 3
        done
        [ -n "$ok" ] || bad="$bad $p"
    done
    if [ -n "$bad" ]; then
        echo "[kt66] ⚠ 데이터센터 콘솔 무응답:${bad} (http://${probe}:PORT/)"
        echo "[kt66]   INT_HOST_IP(=${INT_HOST_IP}) 가 이 서버에서 닿을 수 있는 주소인지 확인하세요."
        echo "[kt66]   이름 경로(http://noc.kt66.lab/ 등)는 :80 으로 들어오므로 그쪽은 열릴 수 있습니다."
    else
        echo "[kt66] 데이터센터 콘솔 6종 응답 확인 (http://${probe}:{8010,8020,8030,8050,8060,8070})"
    fi
}

is_wireless_if() {   # $1=iface — 무선이면 return 0
    [ -d "/sys/class/net/$1/wireless" ] && return 0
    command -v iw >/dev/null 2>&1 && iw dev 2>/dev/null | grep -qw "$1" && return 0
    return 1
}

# 입력한 웹 진입 IP 를 주 이더넷 IF 에 netplan static 으로 고정(재부팅에도 유지).
#   · 유선(브리지 VM)만 지원 — 무선/미탐지/0.0.0.0 은 skip.
#   · 적용 전 확인(원격 SSH 면 IP 변경으로 끊길 수 있어 콘솔 권장). 비대화형은 WEB_NETPLAN_STATIC=1 필요.
#   · 롤백: /etc/netplan/99-kt66-static.yaml 삭제 후 netplan apply (백업은 /etc/netplan/backup-kt66/).
#   · override: WEB_STATIC_IFACE / WEB_STATIC_GW / WEB_STATIC_PREFIX
#   · 테스트 seam: KT66_NETPLAN_DIR / KT66_CLOUDCFG_DIR / KT66_NETPLAN_DRYRUN
netplan_static() {
    local ip="${1:-$WEB_HOST_IP}"
    if [ -z "$ip" ] || [ "$ip" = "0.0.0.0" ]; then
        echo "[kt66] WEB_HOST_IP=${ip:-(미설정)} — 모든 인터페이스 바인딩이라 static 고정 불필요(skip)"; return 0
    fi
    if ! command -v netplan >/dev/null 2>&1; then
        echo "[kt66] netplan 미설치 — 자동 static 고정 skip. VM IP 를 수동으로 ${ip} 고정 권장"; return 0
    fi
    local IFACE GW PREFIX
    IFACE="${WEB_STATIC_IFACE:-$(ip -4 route show default | awk '{print $5; exit}')}"
    [ -z "$IFACE" ] && IFACE="$(ip -4 -br addr | awk '$1!="lo"{print $1; exit}')"
    if [ -z "$IFACE" ]; then echo "[kt66] WARN: 주 인터페이스 미탐지 — netplan static skip"; return 0; fi
    if is_wireless_if "$IFACE"; then
        echo "[kt66] WARN: ${IFACE} 는 무선 — netplan static 자동설정 미지원(유선 브리지 VM 용). skip"; return 0
    fi
    GW="${WEB_STATIC_GW:-$(ip -4 route show default | awk '{for(i=1;i<=NF;i++) if($i=="via"){print $(i+1); exit}}')}"
    PREFIX="${WEB_STATIC_PREFIX:-$(ip -4 -br addr show "$IFACE" 2>/dev/null | awk '{print $3}' | head -1 | cut -d/ -f2)}"
    PREFIX="${PREFIX:-24}"
    [ -z "$GW" ] && GW="$(echo "$ip" | cut -d. -f1-3).1"

    local NPDIR="${KT66_NETPLAN_DIR:-/etc/netplan}"
    local CCDIR="${KT66_CLOUDCFG_DIR:-/etc/cloud/cloud.cfg.d}"
    local NP="$NPDIR/99-kt66-static.yaml"

    echo "[kt66] ── netplan static 고정 계획 ──"
    echo "        인터페이스 : ${IFACE}"
    echo "        주소       : ${ip}/${PREFIX}"
    echo "        게이트웨이 : ${GW}"
    echo "        파일       : ${NP}"
    if [ -z "${KT66_NETPLAN_DRYRUN:-}" ]; then
        if [ -t 0 ]; then
            printf "  적용할까요? 원격 SSH 세션이면 IP 변경으로 끊길 수 있습니다(콘솔 권장) [y/N]: "
            local a; read -r a || a=""
            case "$a" in y|Y|yes|YES) ;; *) echo "[kt66] netplan static 취소 — WEB_HOST_IP 는 .env 에만 고정됨(VM IP 수동 고정 권장)"; return 0 ;; esac
        elif [ -z "${WEB_NETPLAN_STATIC:-}" ]; then
            echo "[kt66] 비대화형 — 네트워크 자동 변경 보류. 적용하려면 WEB_NETPLAN_STATIC=1 로 재실행"; return 0
        fi
    fi

    $SUDO mkdir -p "$NPDIR/backup-kt66"
    local f; for f in "$NPDIR"/*.yaml "$NPDIR"/*.yml; do [ -f "$f" ] && { $SUDO cp -n "$f" "$NPDIR/backup-kt66/" 2>/dev/null || true; }; done
    # cloud-init 네트워크 관리 비활성화 → static 이 재부팅에도 덮이지 않음
    $SUDO mkdir -p "$CCDIR"
    printf 'network: {config: disabled}\n' | $SUDO tee "$CCDIR/99-kt66-disable-network.cfg" >/dev/null
    $SUDO tee "$NP" >/dev/null <<YAML
# kt66 — 웹 진입 고정 IP static (자동 생성)
# 롤백: 이 파일 삭제 후 'sudo netplan apply' (원본 백업: backup-kt66/, cloud-init 재활성은
#       /etc/cloud/cloud.cfg.d/99-kt66-disable-network.cfg 삭제).
network:
  version: 2
  renderer: networkd
  ethernets:
    ${IFACE}:
      dhcp4: false
      addresses: [${ip}/${PREFIX}]
      routes:
        - to: default
          via: ${GW}
      nameservers:
        addresses: [8.8.8.8, 1.1.1.1]
YAML
    $SUDO chmod 600 "$NP"

    if [ -n "${KT66_NETPLAN_DRYRUN:-}" ]; then
        echo "[kt66] (dry-run) 파일 생성만 — netplan generate/apply 생략"; return 0
    fi
    if ! $SUDO netplan generate 2>&1 | sed 's/^/  netplan: /'; then
        echo "[kt66] ERROR: netplan generate 실패 — ${NP} 제거(롤백)"; $SUDO rm -f "$NP"; return 1
    fi
    $SUDO netplan apply && echo "[kt66] ✅ netplan static 적용: ${IFACE}=${ip}/${PREFIX} gw=${GW} (재부팅에도 유지)"
}

ensure_ssh_keys() {
    mkdir -p keys
    if [ ! -f keys/id_rsa ]; then
        ssh-keygen -t ed25519 -f keys/id_rsa -N "" -C "kt66-bastion@auto" >/dev/null 2>&1
        echo "[kt66] SSH 키 생성(keys/id_rsa) — 컨테이너 간 password-less SSH"
    fi
    chmod 600 keys/id_rsa 2>/dev/null || true; chmod 644 keys/id_rsa.pub 2>/dev/null || true
}



ensure_certs() {
    # Wazuh TLS 인증서 생성 (레포 미포함 → fresh 배포 시 생성).  wazuh-certs-generator 사용.
    if [ -f wazuh-config/certs/root-ca.pem ] && [ -f wazuh-config/certs/wazuh.manager.pem ]; then
        echo "[kt66] 인증서 이미 존재 — 생성 건너뜀"; return 0
    fi
    echo "[kt66] Wazuh 인증서 생성 (wazuh-certs-generator)"
    mkdir -p wazuh-config/certs
    docker run --rm \
        -v "$(pwd)/wazuh-config/certs:/certificates/" \
        -v "$(pwd)/wazuh-config/config/certs.yml:/config/certs.yml" \
        wazuh/wazuh-certs-generator:0.0.2 2>&1 | sed 's/^/  /' || true
    # ── 권한 정규화 ── generator 가 디렉터리 0500 / 파일 0400 / UID 999 로 잠금.
    # 동작하는 kt66 레이아웃 = 사용자(uid 1000) 소유 + 644(world-readable). 컨테이너(wazuh uid 1000 등)
    # 가 읽을 수 있어야 함. up 은 root 로 실행되므로 chown/chmod 가능 (REAL_USER=ccc 로 환원).
    $SUDO chown -R "$REAL_USER:$REAL_USER" wazuh-config/certs || true
    $SUDO chmod 755 wazuh-config/certs || true
    $SUDO chmod -R u+rw wazuh-config/certs/* 2>/dev/null || true
    # ── 단일 CA 통일 ── generator 는 indexer/dashboard(root-ca) 와 manager(root-ca-manager) 를
    # 별도 CA 로 만든다 → filebeat(manager)↔indexer mTLS 가 서로 다른 CA 라 실패. manager 인증서를
    # root-ca 로 재발급하여 전 노드가 단일 root-ca 를 신뢰하게 통일한다 (kt66 검증 레이아웃).
    local cd_certs="wazuh-config/certs"
    openssl req -new -key "$cd_certs/wazuh.manager-key.pem" -out /tmp/_mgr.csr \
        -subj "/C=US/L=California/O=Wazuh/OU=Wazuh/CN=wazuh.manager" 2>/dev/null || true
    printf "subjectAltName=DNS:wazuh.manager,DNS:wazuh-manager,DNS:siem,DNS:localhost,IP:127.0.0.1\n" > /tmp/_mgr.ext
    openssl x509 -req -in /tmp/_mgr.csr -CA "$cd_certs/root-ca.pem" -CAkey "$cd_certs/root-ca.key" \
        -CAcreateserial -days 3650 -sha256 -extfile /tmp/_mgr.ext -out "$cd_certs/wazuh.manager.pem" 2>/dev/null || true
    cp -f "$cd_certs/root-ca.pem" "$cd_certs/root-ca-manager.pem"
    cp -f "$cd_certs/root-ca.key" "$cd_certs/root-ca-manager.key"
    rm -f /tmp/_mgr.csr /tmp/_mgr.ext "$cd_certs/root-ca.srl"
    # kt66 동작 모델 = 전부 644(world-readable). 컨테이너 uid 무관하게 읽힘 (lab 인증서).
    $SUDO chmod 644 "$cd_certs"/*.pem "$cd_certs"/*-key.pem "$cd_certs"/*.key 2>/dev/null || true
    $SUDO chown -R "$REAL_USER:$REAL_USER" "$cd_certs" 2>/dev/null || true
    # 검증: manager 가 단일 root-ca 로 verify 되어야 함
    if ! openssl verify -CAfile "$cd_certs/root-ca.pem" "$cd_certs/wazuh.manager.pem" >/dev/null 2>&1; then
        echo "[kt66] ERROR: 인증서 단일 CA 통일 실패 — wazuh.manager.pem 이 root-ca 로 verify 안 됨"; return 1
    fi
    echo "[kt66] 인증서 준비 (단일 CA 통일, verify OK): $(ls "$cd_certs"/*.pem 2>/dev/null | wc -l) .pem"
}

# ───────────────────────────────────────────────── install (Docker + daemon.json)
cmd_install() {
    echo "[kt66] === install: Docker + daemon.json(userland-proxy=false) ==="
    if ! command -v docker >/dev/null 2>&1; then
        curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
        $SUDO sh /tmp/get-docker.sh
        $SUDO usermod -aG docker "$USER" || true
        echo "[kt66] Docker 설치 완료 — docker 그룹 반영 위해 재로그인/새 셸 필요할 수 있음"
    fi
    # daemon.json: userland-proxy=false (출처 IP 보존 핵심) + DNS
    local dj=/etc/docker/daemon.json tmp; tmp=$(mktemp)
    if [ -f "$dj" ] && command -v jq >/dev/null 2>&1; then
        $SUDO jq '. + {"userland-proxy": false, "dns": ["8.8.8.8","1.1.1.1"]}' "$dj" > "$tmp"
    else
        printf '{\n  "userland-proxy": false,\n  "dns": ["8.8.8.8", "1.1.1.1"]\n}\n' > "$tmp"
    fi
    $SUDO cp "$dj" "${dj}.bak.$(date +%s)" 2>/dev/null || true
    $SUDO cp "$tmp" "$dj"; rm -f "$tmp"
    $SUDO systemctl restart docker
    sleep 4
    echo "[kt66] docker: $(docker --version 2>/dev/null)  userland-proxy=false 적용"
    # 최초 setup: 웹 진입 고정 IP 를 사용자에게 1회 질의 → .env 에 고정(이후 up/재부팅 재사용)
    resolve_web_host_ip
    # 입력한 IP 를 유선 IF 에 netplan static 으로 고정(확인 후, 무선/0.0.0.0 은 자동 skip)
    netplan_static "$WEB_HOST_IP"
    echo "[kt66] install 완료 — 다음: (docker 그룹 반영 위해 새 셸에서) ./kt66.sh up"
}

# ───────────────────────────────────────────────── host network glue
cmd_net() { exec ./kt66-net.sh; }

install_systemd() {
    # 호스트 IP alias 를 docker 기동 전에 보장 (재부팅 후 compose 바인딩 가능)
    #
    # 유닛의 ExecStart 경로는 **실제 저장소 위치로 치환한다.** 예전엔 유닛에
    # /home/ccc/kt66 이 박혀 있었는데, 이 저장소는 ~/work/kt66 에 있다. 그래서
    # enable 은 성공하고 실행은 조용히 실패했다 — 그 결과 재부팅마다 망 글루가
    # 사라졌고, 아무도 몰랐다. 경로를 박아 두는 것은 배포에서 늘 이렇게 갚는다.
    local here; here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    sed "s|__KT66_DIR__|$here|g" kt66-hostip.service \
        | $SUDO tee /etc/systemd/system/kt66-hostip.service >/dev/null
    sed "s|__KT66_DIR__|$here|g" kt66-net.service \
        | $SUDO tee /etc/systemd/system/kt66-net.service >/dev/null
    $SUDO systemctl daemon-reload
    $SUDO systemctl enable kt66-hostip >/dev/null 2>&1 || true
    $SUDO systemctl enable --now kt66-net >/dev/null 2>&1 || true
    echo "[kt66] kt66-hostip/kt66-net.service 설치·활성 (재부팅 후 IP alias + 체인 자동 보존)"
}

# ───────────────────────────────────────────────── sigma
cmd_sigma() { (cd sigma && SIEM_CONTAINER=kt66-siem ./install-sigma.sh); }

# ───────────────────────────────────────────────── up (전체)
# kt66 은 core 단일 스택이다. el34 의 misp/opencti/sysmon 오버레이는 메모리 부담이 커
# AI데이터센터 교육 과정에서 제외했다(제거된 파일: docker-compose.{misp,opencti,sysmon,ollama}.yml).
OVERLAY="-f docker-compose.yaml"
ENVF="--env-file .env"

cmd_up() {
    # up 은 root 필요(인증서 권한 정규화 + kt66-net iptables/sysctl + systemd). 비-root 면 sudo 재실행.
    if [ "$(id -u)" != 0 ]; then
        echo "[kt66] up 은 root 권한 필요 — sudo 로 재실행합니다"
        exec sudo -E "$0" up
    fi
    command -v docker >/dev/null || { echo "[kt66] Docker 없음 — 먼저 'sudo ./kt66.sh install'"; exit 1; }
    ensure_env; ensure_ssh_keys; ensure_certs
    resolve_web_host_ip  # install 에서 고정한 웹 진입 IP 사용(.env). 미설정이면 여기서 1회 질의.
    resolve_int_host_ip  # 내부 GUI(관제·주입기·SIEM…) 바인딩 IP. 미설정이면 웹 진입 IP 를 따른다.
    # compose 가 바인딩하는 호스트 IP(웹외부 WEB_HOST_IP / 내부GUI INT_HOST_IP) 보장 — 없으면 core up 이
    # "cannot assign requested address" 로 실패. 실 NIC/DHCP IP 면 멱등 skip.
    WEB_HOST_IP="$WEB_HOST_IP" INT_HOST_IP="$INT_HOST_IP" ./kt66-hostip.sh
    echo "[kt66] === build (최초 ~수GB pull) ==="
    docker compose $OVERLAY $ENVF build
    echo "[kt66] === core up ==="
    docker compose $OVERLAY $ENVF up -d
    # netglue 는 한 번 돌고 끝나는 서비스라 이미 '종료됨' 상태면 up -d 가 다시 안 돌린다.
    # 그런데 매번 돌아야 한다 — docker 데몬이 뜰 때마다 sysctl 이 되돌아가기 때문이다.
    docker compose $OVERLAY $ENVF up -d --force-recreate netglue >/dev/null 2>&1 || true
    ./kt66-net.sh                       # 호스트에서도 한 번 (netglue 가 실패한 경우의 그물)
    # 종료코드 0 정상 · 1 어긋남 · 2 판정불가. 2 를 경고로 처리하면 거짓 경보가 된다.
    ./kt66-net.sh --check; _c=$?
    [ "$_c" = 1 ] && echo "[kt66] WARN: 망 글루 미적용 — 웹 입구가 죽어 있다"
    [ "$_c" = 2 ] && echo "[kt66] 참고: 룰 확인에 root 가 필요하다 — sudo ./kt66-net.sh --check"
    install_systemd
    echo "[kt66] === sigma 적재 ==="
    cmd_sigma || echo "[kt66] WARN: sigma 적재 실패(나중에 ./kt66.sh sigma)"
    # root 로 생성된 사용자-facing 파일을 원 사용자 소유로 환원 (이후 비-root 운영/down 가능하게)
    chown -R "$REAL_USER:$REAL_USER" .env keys 2>/dev/null || true
    check_datacenter_consoles
    check_no_metered_llm
    echo "[kt66] ✅ up 완료. 웹 진입 http://${WEB_HOST_IP}:8001.. / 내부 GUI http://${INT_HOST_IP}:{5601,8000,8081-8083}"
    echo "[kt66]    데이터센터 http://${INT_HOST_IP}:{8010,8020,8030,8050,8060,8070} — 관제는 :8020"
    echo "[kt66]    이름으로도 열린다(학생 hosts 3번째 줄): http://noc.kt66.lab/ 등 — README 참고"
}

cmd_down() { docker compose $OVERLAY $ENVF down "${1:-}" 2>/dev/null || docker compose down "${1:-}"; }

case "${1:-}" in
    install) cmd_install ;;
    up)      cmd_up ;;
    down)    shift; cmd_down "${1:-}" ;;
    net)     cmd_net ;;
    certs)   ensure_certs ;;
    env)     ensure_env ;;
    sigma)   cmd_sigma ;;
    *) echo "usage: $0 {install|up|down [-v]|net|certs|env|sigma}"; exit 1 ;;
esac
