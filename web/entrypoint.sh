#!/bin/bash
set -e

SSH_USER="${SSH_USER:-ccc}"
SSH_PASS="${SSH_PASS:-ccc}"
WAZUH_MANAGER="${WAZUH_MANAGER:-10.20.32.100}"
DEFAULT_GW="${DEFAULT_GW:-10.20.32.1}"

if [[ ! "$WAZUH_MANAGER" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    RES_IP=$(getent hosts "$WAZUH_MANAGER" 2>/dev/null | awk '{print $1}' | head -1)
    [ -n "$RES_IP" ] && WAZUH_MANAGER="$RES_IP" || WAZUH_MANAGER="10.20.32.100"
fi

# Override default route to go via ips (so ext-bound traffic goes back through chain)
echo "[web] setting default route via $DEFAULT_GW (ips)"
ip route del default 2>/dev/null || true
ip route add default via "$DEFAULT_GW" 2>/dev/null || true

if ! id "$SSH_USER" >/dev/null 2>&1; then
    useradd -m -s /bin/bash -G sudo,www-data "$SSH_USER"
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
    echo "[web] authorized_keys deployed — bastion 의 password-less ssh 가능"
fi

# Self-signed cert (generate once)
if [ ! -f /etc/apache2/ssl/server.crt ]; then
    echo "[web] generating self-signed cert"
    mkdir -p /etc/apache2/ssl
    openssl req -x509 -nodes -days 730 \
        -newkey rsa:2048 \
        -keyout /etc/apache2/ssl/server.key \
        -out    /etc/apache2/ssl/server.crt \
        -subj "/CN=*.kt66.lab/O=kt66/C=KR" 2>/dev/null
    chmod 600 /etc/apache2/ssl/server.key
fi

echo "ServerName web" >> /etc/apache2/apache2.conf

# --- Wazuh agent: configure + register + start --------------------------
if [ -d /var/ossec ]; then
    echo "[web] configuring Wazuh agent (manager=$WAZUH_MANAGER)"
    sed -i "s|<address>.*</address>|<address>$WAZUH_MANAGER</address>|" /var/ossec/etc/ossec.conf

    if ! grep -q '/var/log/apache2/modsec_audit.log' /var/ossec/etc/ossec.conf; then
        sed -i '/<\/ossec_config>/i\
  <localfile>\n    <log_format>apache</log_format>\n    <location>/var/log/apache2/access.log</location>\n  </localfile>\n  <localfile>\n    <log_format>apache</log_format>\n    <location>/var/log/apache2/error.log</location>\n  </localfile>\n  <localfile>\n    <log_format>json</log_format>\n    <location>/var/log/apache2/modsec_audit.log</location>\n  </localfile>' /var/ossec/etc/ossec.conf
    fi

    # kt66-assessor: FIM(syscheck) + 명령 로깅 localfile 주입 (정적·cohort-free, 멱등).
    # wazuh-agent.conf.append 의 'kt66-assessor-collection-v2' 블록을 </ossec_config> 앞에 1회 삽입.
    if [ -f /tmp/wazuh-agent.conf.append ] && ! grep -q 'kt66-assessor-collection-v2' /var/ossec/etc/ossec.conf; then
        __awktmp=$(mktemp)
        awk 'NR==FNR{ins=ins $0 ORS; next} /<\/ossec_config>/ && !d{printf "%s",ins; d=1} {print}' \
            /tmp/wazuh-agent.conf.append /var/ossec/etc/ossec.conf > "$__awktmp" && \
            cat "$__awktmp" > /var/ossec/etc/ossec.conf
        rm -f "$__awktmp"
        echo "[web] ★ Assessor 수집(FIM + cmdlog localfile) 주입"
    fi

    echo "[web] waiting for Wazuh manager $WAZUH_MANAGER:1515..."
    for i in $(seq 1 30); do
        if (echo > /dev/tcp/$WAZUH_MANAGER/1515) 2>/dev/null; then
            echo "[web]   manager ready"
            break
        fi
        sleep 2
    done

    /var/ossec/bin/agent-auth -m "$WAZUH_MANAGER" -A "$(hostname)" 2>&1 | tail -3 || \
        echo "[web] WARN: agent-auth failed"

    /var/ossec/bin/wazuh-control start 2>&1 | sed 's/^/  /' || \
        echo "[web] WARN: wazuh-control start failed"
fi

# ── kt66 명령 로깅(채점/감사용, cohort-free 정적) ──────────────────────────
# 대화형 셸 명령을 /var/log/kt66-cmd.log(Wazuh agent localfile) + local6(rsyslog 보유 시) 로 기록.
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

echo "[web] starting apache2"
apache2ctl configtest 2>&1 | sed 's/^/  /' || true
apache2ctl -D FOREGROUND &

# ─── secuops-easy 교육용 GUI: WAF 콘솔 (이미지 내장 → 자동 기동) ──────────────
# 휘발성 docker-exec 주입 대신 entrypoint 에서 영구 기동. down/up·재부팅 후에도
# waf-gui.kt66.lab 가 네트워크/exec 없이 즉시 열린다(HAProxy 라우트는 base config 내장).
if [ -f /opt/modsec_edu_gui/server.py ] && ! pgrep -f /opt/modsec_edu_gui/server.py >/dev/null 2>&1; then
    echo "[web] starting modsec_edu_gui (WAF 콘솔) on :8080"
    python3 /opt/modsec_edu_gui/server.py 8080 >/var/log/modsec_edu_gui.log 2>&1 &
fi

echo "[web] starting sshd"
exec /usr/sbin/sshd -D -e
