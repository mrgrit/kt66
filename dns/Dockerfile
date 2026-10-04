FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends dnsmasq-base iproute2 && rm -rf /var/lib/apt/lists/*
COPY dns/start.sh /start.sh
ENTRYPOINT ["bash", "/start.sh"]
