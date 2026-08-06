# kt66 GPU 게이트웨이 — 3F GPU 존(app)과 DGX Spark 를 잇는 WireGuard 종단.
#
# DGX Spark 는 공인 IP 회선에 있고 랩 호스트는 NAT 뒤에 있다. macvlan(같은 L2 아님)도
# 단순 L3 라우팅(인바운드 불가)도 안 되므로 터널로 잇는다. 이 컨테이너가 랩 쪽 종단이고,
# NAT 뒤이므로 **이쪽이 먼저 연결을 건다**(PersistentKeepalive).
FROM alpine:3.20

RUN apk add --no-cache wireguard-tools iproute2 iptables nftables \
        iputils bind-tools tcpdump curl jq

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
