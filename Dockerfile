# The API (and, with another command, the scheduled jobs).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

COPY requirements.txt .
# Behind a TLS-inspecting proxy (some corporate networks), pass its CA as the
# optional build secret "extra_ca" (EXTRA_CA_FILE in docker-compose.yml).
RUN --mount=type=secret,id=extra_ca \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install -r requirements.txt

COPY app ./app
COPY sample ./sample
COPY docker/jobs.sh ./jobs.sh

RUN useradd --system --uid 10001 floor && chown -R floor /srv
USER floor

EXPOSE 8000
# Create any missing tables, then serve. Behind a load balancer, set TRUST_PROXY=true.
CMD ["sh", "-c", "python -m app.db && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
