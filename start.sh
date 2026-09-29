#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
if [[ ! -f .env ]]; then cp .env.example .env; echo "请填写 .env 后重新运行"; exit 1; fi
docker compose config >/dev/null
docker compose up -d --build postgres neo4j geoserver
docker compose run --rm geoserver-init
docker compose run --rm graph-init
docker compose run --rm document-index-init
docker compose up -d --build backend frontend
port="${HTTP_PORT:-8088}"
for _ in $(seq 1 60); do
  if curl --fail --silent "http://127.0.0.1:${port}/health/ready" >/dev/null; then echo "部署成功: http://127.0.0.1:${port}"; exit 0; fi
  sleep 5
done
docker compose ps
docker compose logs --tail 100 backend
exit 1
