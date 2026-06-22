#!/bin/bash
set -e

echo "=== 宏曦标书 ==="
echo "Waiting for PostgreSQL..."
until python3 -c "import socket; s=socket.socket(); s.settimeout(2); s.connect(('db',5432)); s.close()" 2>/dev/null; do
  echo "  still waiting..."
  sleep 2
done
echo "PostgreSQL is ready."

echo "Starting application..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
