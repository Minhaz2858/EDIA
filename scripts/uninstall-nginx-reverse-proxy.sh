#!/usr/bin/env bash
set -euo pipefail

target_conf="${NGINX_EDIA_CONF:-/www/server/panel/vhost/nginx/edia.conf}"
nginx_bin="${NGINX_BIN:-/www/server/nginx/sbin/nginx}"
nginx_main_conf="${NGINX_MAIN_CONF:-/www/server/nginx/conf/nginx.conf}"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run this script with sudo or as root." >&2
  echo "Example: sudo bash ./scripts/uninstall-nginx-reverse-proxy.sh" >&2
  exit 1
fi

rm -f "$target_conf"

"$nginx_bin" -t -c "$nginx_main_conf"
"$nginx_bin" -s reload -c "$nginx_main_conf"

echo "Removed Nginx reverse proxy config: $target_conf"