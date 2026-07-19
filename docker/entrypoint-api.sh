#!/bin/sh
# API entrypoint: run DB migrations, then start the server.
set -e

echo "Running database migrations..."
alembic -c /srv/service/alembic.ini upgrade head

echo "Starting API..."
exec uvicorn service.app.main:app --host 0.0.0.0 --port 8000 --workers 2
