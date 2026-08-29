#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_conf="$repo_root/docs/nginx/edia-reverse-proxy.conf"
target_conf="${NGINX_EDIA_CONF:-/www/server/panel/vhost/nginx/edia.conf}"
nginx_bin="${NGINX_BIN:-/www/server/nginx/sbin/nginx}"
nginx_main_conf="${NGINX_MAIN_CONF:-/www/server/nginx/conf/nginx.conf}"
publish_root="${EDIA_STATIC_ROOT:-/var/tmp/edia_static}"

if [[ ! -f "$source_conf" ]]; then
  echo "Source config not found: $source_conf" >&2
  exit 1
fi

if [[ ! -x "$nginx_bin" ]]; then
  echo "Nginx binary not found: $nginx_bin" >&2
  exit 1
fi

if [[ ! -f "$publish_root/index.html" ]]; then
  echo "Static frontend not published yet: $publish_root/index.html" >&2
  echo "Run: bash ./scripts/publish-frontend-static.sh" >&2
  exit 1
fi

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run this script with sudo or as root." >&2
  echo "Example: sudo bash ./scripts/install-nginx-reverse-proxy.sh" >&2
  exit 1
fi

install -d "$(dirname "$target_conf")"
install -m 0644 "$source_conf" "$target_conf"

"$nginx_bin" -t -c "$nginx_main_conf"
"$nginx_bin" -s reload -c "$nginx_main_conf"

echo "Installed Nginx reverse proxy config: $target_conf"
echo "Public URL: http://YOUR_SERVER_IP:8099/"