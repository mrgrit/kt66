FROM debian:bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive TZ=Asia/Seoul
RUN printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d && chmod +x /usr/sbin/policy-rc.d && \
    apt-get update && apt-get install -y --no-install-recommends postfix dovecot-imapd dovecot-lmtpd openssl ca-certificates python3 iproute2 rsyslog supervisor tzdata && \
    rm -rf /var/lib/apt/lists/* && groupadd -g 5000 vmail && useradd -u 5000 -g vmail -d /var/mail/vhosts -s /usr/sbin/nologin vmail
COPY mail/ /opt/kt66-mail/
COPY deployment/configure.py /opt/kt66-mail/domain.py
EXPOSE 25 587 993
ENTRYPOINT ["python3", "/opt/kt66-mail/start.py"]
