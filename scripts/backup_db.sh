#!/bin/sh
set -eu
cd /root/lavka
mkdir -p /root/lavka-db-backups
ts=$(date -u +%Y%m%dT%H%M%SZ)
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > "/root/lavka-db-backups/lavka-$ts.sql.gz"
test -s "/root/lavka-db-backups/lavka-$ts.sql.gz"
find /root/lavka-db-backups -type f -name "lavka-*.sql.gz" -mtime +14 -delete
echo "BACKUP_OK /root/lavka-db-backups/lavka-$ts.sql.gz"
