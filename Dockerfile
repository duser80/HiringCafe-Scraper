# HiringCafe Jobs API service image. Runs the API by default; the worker
# overrides the command in docker-compose.yml.
FROM python:3.12.8-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY service/requirements.txt /srv/service-requirements.txt
RUN pip install --no-cache-dir -r /srv/service-requirements.txt

COPY hiringcafe_scraper/ /srv/hiringcafe_scraper/
COPY service/ /srv/service/
COPY docker/entrypoint-api.sh /srv/entrypoint-api.sh
RUN chmod +x /srv/entrypoint-api.sh

# Non-root runtime user
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /srv
USER appuser

EXPOSE 8000

CMD ["/srv/entrypoint-api.sh"]
