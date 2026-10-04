#!/bin/bash
set -euo pipefail
case "${1:-}" in
fw)
  ip route replace 10.20.70.0/24 via 10.20.31.2
  uplink=$(ip -o -4 addr show | awk '$4 ~ /^10\.20\.30\./ {print $2; exit}')
  test -n "$uplink"
  nft -f - <<EOF
add table ip kt66user_nat
flush table ip kt66user_nat
table ip kt66user_nat {
  chain postrouting {
    type nat hook postrouting priority 110; policy accept;
    ip saddr 10.20.70.0/24 oifname "$uplink" counter masquerade
  }
}
EOF
  ;;
ips)
  # 다른 존의 정책은 변경하지 않고 사용자망의 접근만 제한한다.
  nft -f - <<'EOF'
add table inet kt66user
flush table inet kt66user
table inet kt66user {
  chain forward {
    type filter hook forward priority 5; policy accept;
    ip saddr 10.20.70.0/24 ct state established,related counter accept
    ip daddr 10.20.70.0/24 ct state established,related counter accept
    ip saddr 10.20.70.0/24 ip daddr 10.20.32.100 tcp dport {1514,1515} counter accept
    ip saddr 10.20.70.0/24 ip daddr 10.20.32.53 udp dport 53 counter accept
    ip saddr 10.20.70.0/24 ip daddr 10.20.32.53 tcp dport 53 counter accept
    ip saddr 10.20.70.0/24 ip daddr 10.20.32.25 tcp dport {25,587,993} counter accept
    ip saddr 10.20.70.0/24 ip daddr 10.20.32.80 tcp dport {80,443,8091} counter accept
    ip saddr 10.20.30.202 ip daddr 10.20.70.10 tcp dport {3389,8080} counter accept
    ip saddr 10.20.30.202 ip daddr 10.20.70.10 ip protocol icmp counter accept
    ip daddr 10.20.70.0/24 counter drop
    ip saddr 10.20.70.0/24 ip daddr {10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,169.254.0.0/16} counter drop
    ip saddr 10.20.70.0/24 tcp dport {53,80,443} counter accept
    ip saddr 10.20.70.0/24 udp dport {53,123} counter accept
    ip saddr 10.20.70.0/24 counter drop
  }
}
add table ip kt66user_manager
flush table ip kt66user_manager
table ip kt66user_manager {
  chain postrouting {
    type nat hook postrouting priority 91; policy accept;
    ip saddr 10.20.70.0/24 ip daddr 10.20.32.100 tcp dport {1514,1515} counter masquerade
  }
}
EOF
  ;;
*) echo '사용법: user-network.sh fw|ips' >&2; exit 2;;
esac
