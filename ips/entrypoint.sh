#!/bin/bash
set -e

SSH_USER="${SSH_USER:-ccc}"
SSH_PASS="${SSH_PASS:-ccc}"
WAZUH_MANAGER="${WAZUH_MANAGER:-10.20.32.100}"
FW_PIPE_IP="${FW_PIPE_IP:-10.20.31.1}"
GPU_GW_IP="${GPU_GW_IP:-10.20.50.2}"     # 3F GPU 게이트웨이(WireGuard 종단)
DGX_LAB_IP="${DGX_LAB_IP:-10.20.50.10}"  # 터널 너머 DGX Spark

if ! id "$SSH_USER" >/dev/null 2>&1; then
    useradd -m -s /bin/bash -G sudo "$SSH_USER"
    echo "${SSH_USER}:${SSH_PASS}" | chpasswd
    echo "$SSH_USER ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/$SSH_USER
fi

# bastion pubkey → ccc authorized_keys (ProxyJump 의 2-hop)
if [ -f /keys/id_rsa.pub ]; then
    mkdir -p /home/$SSH_USER/.ssh
    cat /keys/id_rsa.pub > /home/$SSH_USER/.ssh/authorized_keys
    chown -R $SSH_USER:$SSH_USER /home/$SSH_USER/.ssh
    chmod 700 /home/$SSH_USER/.ssh
    chmod 600 /home/$SSH_USER/.ssh/authorized_keys
    echo "[ips] authorized_keys deployed — bastion 의 password-less ssh 가능"
fi

# ─── Routing: ext (10.20.30/24) -> back via fw on pipe ────
echo "[ips] adding return route to ext via fw $FW_PIPE_IP"
ip route add 10.20.30.0/24 via "$FW_PIPE_IP" 2>/dev/null || true

# ─── 3F GPU 존: 터널 너머 DGX Spark 로 가는 /32 호스트 라우트 ───────────
# DGX 주소(10.20.50.10)는 app 존과 같은 /24 안에 있다. 그대로 두면 커널이 연결된
# 세그먼트로 보고 ARP 를 쏴서 응답을 못 받는다. /32 가 연결된 /24 보다 longest-prefix
# 로 우선하므로, 이 한 줄이 "DGX 로 가려면 gpu-gw 를 거쳐라"를 성립시킨다.
# (proxy_arp 를 쓰지 않는 이유 — 명시적 라우트가 예측 가능하고 디버깅이 쉽다.)
ip route replace "$DGX_LAB_IP/32" via "$GPU_GW_IP" 2>/dev/null && \
    echo "[ips] GPU 존: $DGX_LAB_IP -> gpu-gw($GPU_GW_IP)" || \
    echo "[ips] GPU 존 라우트 보류 — gpu-gw 미기동(정상, 나중에 재적용)"

# ─── 출처 IP 보존 vs (legacy) masquerade ──────────────────
# PRESERVE_SRC_IP=1 (기본): masquerade 안 함 → 공격자 출처(.202)가 web/ModSec 까지 보존.
#   리턴 경로는 default GW=fw 로 보장: web→ips→fw→host(conntrack 역-DNAT)→.202.
DMZ_IFACE=$(ip -o -4 addr show | awk '$4 ~ /^10\.20\.32\./ {print $2; exit}')

# ─── Wazuh 에이전트(ext/pipe) → manager 전용 masquerade (항상) ──────────────
# fw(pipe) 등 non-dmz 에이전트는 manager(dmz)로 갈 때 forward 는 ips 경유, return 은
# siem→host 경유로 *비대칭* 이 된다. 호스트가 forward 를 안 보므로 conntrack 이 깨져
# TCP 핸드셰이크는 되도 SSL/데이터 단계에서 끊긴다(agent enroll "SSL error (5)").
# manager IP 로 향하는 에이전트 트래픽만 ips 가 masquerade → siem 이 ips(dmz)로 직접
# 회신 → 경로 대칭화. daddr 를 manager 로 한정하므로 web(.80) 행 공격 트래픽의 출처
# IP 보존(PRESERVE_SRC_IP)에는 영향 없음.
if [ -n "$DMZ_IFACE" ]; then
    nft "add table ip kt66mgr" 2>/dev/null || true
    nft "add chain ip kt66mgr postrouting { type nat hook postrouting priority 90 ; }" 2>/dev/null || true
    nft "add rule ip kt66mgr postrouting oifname \"$DMZ_IFACE\" ip daddr $WAZUH_MANAGER ip saddr 10.20.30.0/24 masquerade" 2>/dev/null || true
    nft "add rule ip kt66mgr postrouting oifname \"$DMZ_IFACE\" ip daddr $WAZUH_MANAGER ip saddr 10.20.31.0/24 masquerade" 2>/dev/null || true
    # 3F GPU 존도 같은 비대칭 문제를 겪는다 — siem 의 회신이 호스트로 새면 SSL 이 끊긴다.
    nft "add rule ip kt66mgr postrouting oifname \"$DMZ_IFACE\" ip daddr $WAZUH_MANAGER ip saddr 10.20.50.0/24 masquerade" 2>/dev/null || true
    nft "add rule ip kt66mgr postrouting oifname \"$DMZ_IFACE\" ip daddr $WAZUH_MANAGER ip saddr 10.20.60.0/24 masquerade" 2>/dev/null || true

    # ─── 3F 모델 운영(app) 행 관리 접근 — 같은 비대칭 문제의 반대 방향 ──────────
    # web 이 modelops.kt66.lab 을 프록시하려면 dmz(10.20.32.80) → app(10.20.50.60) 로
    # 나가야 한다. 가는 길은 ips 가 라우팅한다. 그런데 modelops 의 기본 GW 는 도커
    # 브리지(10.20.50.254 = 호스트)라 **회신이 호스트로 샌다** — 도커의 브리지 간
    # 격리 규칙이 그걸 버리므로 연결이 아예 안 선다(web→10.20.50.60:8000 = 000).
    # envsim 처럼 dmz 다리를 하나 더 붙이는 방법도 있지만, 그러면 3F GPU 존이 dmz 에
    # 걸쳐 있게 되어 "존 경계" 라는 이 랩의 문장이 흐려진다. 목적지 하나만 masquerade
    # 해서 경로를 대칭으로 만든다 — modelops 는 회신을 직접 붙어 있는 ips(10.20.50.1)
    # 로 보내고, 존 구조는 그대로 남는다.
    APP_IFACE=$(ip -o -4 addr show | awk '$4 ~ /^10\.20\.50\./ {print $2; exit}')
    if [ -n "$APP_IFACE" ]; then
        nft "add rule ip kt66mgr postrouting oifname \"$APP_IFACE\" ip daddr 10.20.50.60 tcp dport 8000 masquerade" 2>/dev/null || true
        echo "[ips] modelops(10.20.50.60:8000) 행 masquerade 활성 — app 존 회신 경로 대칭화"
    fi

# ─── 1F 시설망(OT) 접근통제 ────────────────────────────────────────────
# 실제 DC 에서 BMS/시설 계통은 업무망과 분리한다. 여기서도 같다 —
# 외부망(ext)은 시설망에 아예 닿지 못하고, 나머지 존은 시뮬레이터 API(8000)만 열린다.
# 시설망이 뚫리면 냉방·전력을 조작당한다는 것을 정책으로 보여주는 자리다.
nft "add table inet kt66ot" 2>/dev/null || true
nft "add chain inet kt66ot forward { type filter hook forward priority 10 ; policy accept ; }" 2>/dev/null || true
nft "add rule inet kt66ot forward ct state established,related accept" 2>/dev/null || true
nft "add rule inet kt66ot forward ip saddr 10.20.60.0/24 accept" 2>/dev/null || true
# ext 차단이 **포트 허용보다 먼저** 와야 한다. nftables 는 위에서부터 평가하므로
# 8000/ICMP 허용을 앞에 두면 외부망이 그 두 경로로 시설망에 닿는다 — 그러면
# "외부망 전면 차단" 이라는 이 정책의 문장 자체가 거짓이 되고, SEC-04 도 성립하지 않는다.
nft "add rule inet kt66ot forward ip saddr 10.20.30.0/24 ip daddr 10.20.60.0/24 counter log prefix \"[kt66-ips] ext->ot DROP \" drop" 2>/dev/null || true
# 나머지 존(dmz/int/app)은 시뮬레이터 API 와 ICMP 만.
nft "add rule inet kt66ot forward ip daddr 10.20.60.0/24 tcp dport 8000 accept" 2>/dev/null || true
nft "add rule inet kt66ot forward ip daddr 10.20.60.0/24 ip protocol icmp accept" 2>/dev/null || true
nft "add rule inet kt66ot forward ip daddr 10.20.60.0/24 counter log prefix \"[kt66-ips] ->ot DROP \" drop" 2>/dev/null || true
echo "[ips] 1F 시설망(OT) 접근통제 활성 — 외부망(ext) 전면 차단, 그 외 존은 API(8000)/ICMP 만"
    echo "[ips] 에이전트→manager($WAZUH_MANAGER) masquerade 활성 (비대칭 경로 SSL 끊김 방지)"
fi

if [ "${PRESERVE_SRC_IP:-1}" = "1" ]; then
    echo "[ips] PRESERVE_SRC_IP=1 → masquerade 비활성, default GW = fw ($FW_PIPE_IP) for return path"
    ip route replace default via "$FW_PIPE_IP" 2>/dev/null || true
else
    echo "[ips] (legacy) enabling NAT masquerade on dmz NIC $DMZ_IFACE"
    nft "add table ip natkt66" 2>/dev/null || true
    nft "add chain ip natkt66 postrouting { type nat hook postrouting priority 100 ; }" 2>/dev/null || true
    nft "add rule ip natkt66 postrouting oifname \"$DMZ_IFACE\" ip saddr 10.20.30.0/24 masquerade" 2>/dev/null || true
    nft "add rule ip natkt66 postrouting oifname \"$DMZ_IFACE\" ip saddr 10.20.31.0/24 masquerade" 2>/dev/null || true
fi
# int (10.20.40/24) is reached via web (dmz NIC = 10.20.32.80) — but web does L7
# proxy, not L3 forward. ips doesn't need a route to int — incoming TCP to dmz
# 10.20.32.80 (web) terminates there.

# ─── Suricata 룰 update + sniff both pipe + dmz ────────────
echo "[ips] updating Suricata rules (5-10s)"
suricata-update --no-test 2>&1 | tail -3 || true

if ! grep -q 'local.rules' /etc/suricata/suricata.yaml; then
    sed -i 's|^rule-files:|rule-files:\n  - local.rules|' /etc/suricata/suricata.yaml || true
fi

# secuops/W05-S5 (suppress/threshold.config) 동작 보장 — default 주석 해제
sed -i 's|^# threshold-file:|threshold-file:|' /etc/suricata/suricata.yaml || true
# 빈 threshold.config 보장 (W05 의 학생이 학습 후 채움)
[ -f /etc/suricata/threshold.config ] || touch /etc/suricata/threshold.config

# stats event = 333 field → wazuh JSON_Decoder 의 256 limit 초과 → "Too many fields"
# noise (8초 주기 + alert 가치 없음) 이므로 eve-log 의 types 에서 stats: block 제거.
if grep -q "^        - stats:" /etc/suricata/suricata.yaml; then
    sed -i '/^        - stats:$/,/^            deltas:/d' /etc/suricata/suricata.yaml
    echo "[ips] suricata eve-log: stats event_type disabled (wazuh JSON_Decoder 256 limit)"
fi

# Detect interfaces
PIPE_IFACE=$(ip -o -4 addr show | awk '$4 ~ /^10\.20\.31\./ {print $2; exit}')
DMZ_IFACE=$(ip -o -4 addr show | awk '$4 ~ /^10\.20\.32\./ {print $2; exit}')
echo "[ips] sniff interfaces: pipe=$PIPE_IFACE dmz=$DMZ_IFACE"

mkdir -p /var/log/suricata
# af-packet on both interfaces (forward path is in pipe→dmz, return is dmz→pipe)
suricata -i "$PIPE_IFACE" -i "$DMZ_IFACE" -c /etc/suricata/suricata.yaml \
    --runmode autofp -l /var/log/suricata \
    > /var/log/suricata/stdout.log 2>&1 &

# ─── Wazuh agent ───────────────────────────────────────
if [ -d /var/ossec ]; then
    echo "[ips] configuring Wazuh agent (manager=$WAZUH_MANAGER)"
    sed -i "s|<address>.*</address>|<address>$WAZUH_MANAGER</address>|" /var/ossec/etc/ossec.conf

    if ! grep -q '/var/log/suricata/eve.json' /var/ossec/etc/ossec.conf; then
        sed -i '/<\/ossec_config>/i\
  <localfile>\n    <log_format>json</log_format>\n    <location>/var/log/suricata/eve.json</location>\n  </localfile>\n  <localfile>\n    <log_format>syslog</log_format>\n    <location>/var/log/syslog</location>\n  </localfile>' /var/ossec/etc/ossec.conf
    fi

    # kt66-assessor: FIM(suricata 설정/룰 + 실습 디렉터리) + 명령 로깅 localfile (정적·cohort-free, 멱등)
    if ! grep -q 'kt66-assessor-collection-v2' /var/ossec/etc/ossec.conf; then
        __fimblk=$(mktemp)
        cat > "$__fimblk" <<'FIMBLK'
  <!-- kt66-assessor-collection-v2: FIM + cmdlog localfile (정적·cohort-free) -->
  <syscheck>
    <disabled>no</disabled>
    <frequency>300</frequency>
    <scan_on_start>yes</scan_on_start>
    <directories realtime="yes" report_changes="yes" whodata="yes">/etc/suricata</directories>
    <directories realtime="yes" report_changes="yes">/home/ccc</directories>
    <!-- 지속성이 실제로 심기는 자리. /etc 전체는 12시간 주기라 한 교시 안에 안 뜬다.
         여기만 실시간으로 본다 — 흔적을 남겨 놓고 SIEM 이 조용하면 실습이 성립하지 않는다. -->
    <directories realtime="yes" report_changes="yes" whodata="yes">/etc/cron.d</directories>
    <directories realtime="yes" report_changes="yes" whodata="yes">/etc/sudoers.d</directories>
    <directories realtime="yes" report_changes="yes" whodata="yes">/etc/ld.so.preload</directories>
    <directories realtime="yes" report_changes="yes" whodata="yes">/etc/passwd</directories>
    <directories realtime="yes" report_changes="yes">/var/tmp</directories>
    <directories realtime="yes" report_changes="yes">/dev/shm</directories>
  </syscheck>
  <localfile>
    <log_format>syslog</log_format>
    <location>/var/log/kt66-cmd.log</location>
  </localfile>
FIMBLK
        __awktmp=$(mktemp)
        awk 'NR==FNR{ins=ins $0 ORS; next} /<\/ossec_config>/ && !d{printf "%s",ins; d=1} {print}' \
            "$__fimblk" /var/ossec/etc/ossec.conf > "$__awktmp" && \
            cat "$__awktmp" > /var/ossec/etc/ossec.conf
        rm -f "$__fimblk" "$__awktmp"
        echo "[ips] ★ Assessor 수집(FIM + cmdlog localfile) 주입"
    fi

    echo "[ips] waiting for Wazuh manager $WAZUH_MANAGER:1515..."
    for i in $(seq 1 30); do
        if (echo > /dev/tcp/$WAZUH_MANAGER/1515) 2>/dev/null; then
            echo "[ips]   manager ready"
            break
        fi
        sleep 2
    done

    /var/ossec/bin/agent-auth -m "$WAZUH_MANAGER" -A "$(hostname)" 2>&1 | tail -3 || true
    /var/ossec/bin/wazuh-control start 2>&1 | sed 's/^/  /' || true
fi

# ── kt66 명령 로깅(채점/감사용, cohort-free 정적) ──────────────────────────
: > /var/log/kt66-cmd.log 2>/dev/null || true
chmod 0666 /var/log/kt66-cmd.log 2>/dev/null || true
cat > /etc/profile.d/kt66-cmdlog.sh <<'CMDLOG'
# kt66: 대화형 셸 명령 로깅(채점/감사). CC/tubewar 가 Assessor command_ran 으로 질의.
case "$-" in *i*) ;; *) return 2>/dev/null ;; esac
__kt66_cmdlog() {
  local rc=$? last
  last=$(history 1 2>/dev/null | sed 's/^ *[0-9]* *//')
  [ -z "$last" ] && return
  local msg="CMDKT66 host=$(hostname) user=${USER:-?} pwd=$PWD rc=$rc cmd=$last"
  logger -p local6.info -t kt66audit "$msg" 2>/dev/null
  printf '%s %s kt66audit: %s\n' "$(date '+%b %e %H:%M:%S')" "$(hostname)" "$msg" >> /var/log/kt66-cmd.log 2>/dev/null
}
case ";${PROMPT_COMMAND};" in
  *__kt66_cmdlog*) ;;
  *) PROMPT_COMMAND="__kt66_cmdlog;${PROMPT_COMMAND}" ;;
esac
CMDLOG

# ─── secuops-easy 교육용 GUI: IPS 콘솔 (이미지 내장 → 자동 기동) ──────────────
# 휘발성 docker-exec 주입 대신 entrypoint 에서 영구 기동. down/up·재부팅 후에도
# ips-gui.kt66.lab 가 네트워크/exec 없이 즉시 열린다(HAProxy 라우트는 base config 내장).
if [ -f /opt/suricata_edu_gui/server.py ] && ! pgrep -f /opt/suricata_edu_gui/server.py >/dev/null 2>&1; then
    echo "[ips] starting suricata_edu_gui (IPS 콘솔) on :8080"
    python3 /opt/suricata_edu_gui/server.py 8080 >/var/log/suricata_edu_gui.log 2>&1 &
fi

echo "[ips] starting sshd"
exec /usr/sbin/sshd -D -e
