# Base images pinned by digest (hardening spec, decision 7). Update: docker pull <tag>, then copy the new digest.
# --- web page (React) build: Node only exists in this stage ---
FROM node:22-alpine@sha256:1314fb30ec37592d5845e5a160d522c0e680972aa534549940b8d73bde88481a AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund --loglevel=error
COPY web/ ./
RUN npm run build

# --- app ---
FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 WEB_DIST=/app/web/dist REPORTS_DIR=/data/reports BACKUP_DIR=/backup
# Pango + HarfBuzz for WeasyPrint (PDF reports, Arabic shaping); the Arabic font is bundled in callsight/reports/fonts.
# postgresql-client-16 (pg_dump/pg_restore for backups) from the PostgreSQL apt repo: Debian's own client is another
# major version and must not dump the v16 server. The repo key is checked against its published fingerprint.
# Exact pin. PGDG keeps only the newest 16.x in its main repo: when a build says "version not found",
# bump this to the new 16.x (dpkg-query -W postgresql-client-16) - always major 16 = the server's major.
ARG PG_CLIENT_VERSION=16.16-1.pgdg13+1
ARG PGDG_KEY_FPR=B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && gpg --show-keys --with-colons /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc | grep -q "^fpr:::::::::${PGDG_KEY_FPR}:" \
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
       "postgresql-client-16=${PG_CLIENT_VERSION}" \
    && apt-get purge -y --auto-remove curl gnupg \
    && rm -rf /var/lib/apt/lists/*
# Exact versions + sha256 hashes (tools/lock-deps.sh); --no-deps: nothing outside the lock can be pulled in.
# Test images: docker build --build-arg INSTALL_DEV=true (adds pytest + pypdf, also hash-checked).
COPY requirements.lock requirements-dev.lock ./
ARG INSTALL_DEV=false
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements.lock \
    && if [ "$INSTALL_DEV" = "true" ]; then pip install --no-cache-dir --require-hashes --no-deps -r requirements-dev.lock; fi \
    && pip check
COPY alembic.ini pyproject.toml ./
COPY callsight ./callsight
COPY config ./config
COPY --from=web /web/dist ./web/dist
RUN useradd -m -u 1000 app && mkdir -p /data/audio /data/reports /backup /live-state && chown app /data/audio /data/reports /backup /live-state && chmod 700 /live-state
USER app
