#!/usr/bin/env bash
# Database backup: a compressed dump in backups/, keeping the last 7 days.
# Runs daily from cron (set up by deploy/server-setup.sh). Restore: see DEPLOY.md.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
file="backups/copilot-$(date +%F).sql.gz"
docker compose -f docker-compose.prod.yml exec -T postgres pg_dump -U copilot --clean --if-exists copilot \
  | gzip > "$file.part"
mv "$file.part" "$file"
find backups -name 'copilot-*.sql.gz' -mtime +7 -delete
echo "$(date -Is) backup written: $file ($(du -h "$file" | cut -f1))"
