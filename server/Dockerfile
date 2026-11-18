# Один образ для всех ролей: api, radius, worker, migrate (команда задаётся в compose).
FROM python:3.13-slim-bookworm

ARG MFA_GATE_VERSION=0.0.0-dev
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 MFA_GATE_VERSION=${MFA_GATE_VERSION}

RUN groupadd --system --gid 10001 mfagate && useradd --system --uid 10001 --gid mfagate --home /app --shell /usr/sbin/nologin mfagate
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY alembic.ini ./
COPY migrations ./migrations
COPY mfa_gate ./mfa_gate
COPY web ./web

USER mfagate
EXPOSE 8080 1812/udp
ENTRYPOINT ["python", "-m", "mfa_gate"]
CMD ["api"]
