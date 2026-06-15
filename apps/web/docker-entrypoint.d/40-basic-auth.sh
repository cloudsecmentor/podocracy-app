#!/bin/sh
set -eu

if [ -n "${PORTAL_ADMIN_PASSWORD:-}" ]; then
  HASHED_PASSWORD="$(openssl passwd -apr1 "$PORTAL_ADMIN_PASSWORD")"
  printf 'admin:%s\n' "$HASHED_PASSWORD" > /etc/nginx/.htpasswd
  sed -i 's|#AUTH#|auth_basic "Podocracy";\n    auth_basic_user_file /etc/nginx/.htpasswd;|' /etc/nginx/conf.d/default.conf
else
  sed -i 's|#AUTH#||' /etc/nginx/conf.d/default.conf
fi
