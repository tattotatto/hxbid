#!/bin/bash
set -e

echo "=== 宏曦标书 ==="
echo "Waiting for PostgreSQL..."
until pg_isready -h db -U hongxi -d hongxi_bid -q 2>/dev/null; do
  sleep 2
done
echo "PostgreSQL is ready."

echo "Starting application..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
