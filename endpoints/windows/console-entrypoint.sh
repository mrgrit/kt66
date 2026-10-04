#!/bin/sh
set -eu
envsubst '${CONSOLE_BIND} ${CONSOLE_PORT}' < /opt/console.conf.template > /tmp/nginx.conf
exec nginx -c /tmp/nginx.conf -g 'daemon off;'
