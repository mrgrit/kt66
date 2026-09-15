#!/bin/sh
set -eu
cp /opt/kt66-events-rules.xml /var/ossec/etc/rules/kt66-events-rules.xml
chown root:wazuh /var/ossec/etc/rules/kt66-events-rules.xml
chmod 660 /var/ossec/etc/rules/kt66-events-rules.xml
