#!/bin/bash
set -euo pipefail
# 내부 응답은 IPS로, LAN publish 응답은 호스트로 복귀한다.
ip route replace 10.20.0.0/16 via 10.20.32.1
ip route replace default via 10.20.32.254
/usr/sbin/dnsmasq --keep-in-foreground --conf-file=/config/dns-internal.conf --pid-file=/run/dns-internal.pid &
first=$!
/usr/sbin/dnsmasq --keep-in-foreground --conf-file=/config/dns-lan.conf --pid-file=/run/dns-lan.pid &
second=$!
trap 'kill "$first" "$second" 2>/dev/null || true; wait || true' EXIT TERM INT
wait -n "$first" "$second"
exit 1
