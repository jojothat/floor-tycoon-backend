# Space Readout: static dashboard behind nginx, /api proxied to the backend so the browser talks to one origin.
FROM nginx:1.27-alpine
COPY public/ /usr/share/nginx/html/
COPY docker/readout.nginx.conf.template /etc/nginx/templates/default.conf.template
COPY docker/40-write-config.sh /docker-entrypoint.d/40-write-config.sh
RUN chmod +x /docker-entrypoint.d/40-write-config.sh
ENV API_UPSTREAM=http://api:8000 \
    RO_API_BASE=/api \
    RO_READ_TOKEN= \
    RO_SAMPLE=0
EXPOSE 80
