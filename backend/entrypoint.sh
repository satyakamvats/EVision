#!/bin/sh
set -eu

echo "Applying database migrations..."
python -m alembic upgrade head

echo "Starting EVision API..."
exec "$@"
