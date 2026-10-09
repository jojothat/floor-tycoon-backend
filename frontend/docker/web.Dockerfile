# The game: static files behind nginx, with /api proxied to the backend so the phone talks to one origin.
FROM nginx:1.27-alpine
COPY public/ /usr/share/nginx/html/
COPY docker/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY docker/40-write-config.sh /docker-entrypoint.d/40-write-config.sh
RUN chmod +x /docker-entrypoint.d/40-write-config.sh
ENV API_UPSTREAM=http://api:8000 \
    FT_API_BASE=/api \
    FT_PILOT_TOKEN=dev-pilot \
    FT_GAME_VERSION=dev \
    FT_SIM_MS=20000
EXPOSE 80
