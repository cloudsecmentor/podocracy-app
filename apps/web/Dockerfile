FROM nginx:1.27-alpine

RUN apk add --no-cache openssl

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.d/ /docker-entrypoint.d/
COPY index.html improved.html styles.css app.js improved.js pwa.js sw.js manifest.webmanifest favicon.svg icon-192.png icon-512.png apple-touch-icon.png /usr/share/nginx/html/
