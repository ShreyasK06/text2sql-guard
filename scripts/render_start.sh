#!/bin/sh
# Boot sequence for the single-container deployment (Dockerfile.render):
#   1. create a fresh PostgreSQL cluster bound to 127.0.0.1 (not reachable from outside the container)
#   2. run db/init/*.sql as the superuser (roles, grants, row-level security)
#   3. seed the synthetic data
#   4. start the API on $PORT (Render sets it; defaults to 10000)
set -eu

PGDATA=/var/lib/postgresql/data
PGPORT_LOCAL="${T2S_DB_PORT:-54329}"

rm -rf "$PGDATA"/* 2>/dev/null || true
initdb -D "$PGDATA" -U postgres --auth-local=trust --auth-host=scram-sha-256 >/dev/null
pg_ctl -D "$PGDATA" -o "-c listen_addresses=127.0.0.1 -p $PGPORT_LOCAL -c max_connections=40" -w -l /tmp/pg.log start >/dev/null

createdb -h 127.0.0.1 -p "$PGPORT_LOCAL" -U postgres t2s
for f in /srv/db/init/*.sql; do
  echo "init: $f"
  psql -h 127.0.0.1 -p "$PGPORT_LOCAL" -U postgres -d t2s -v ON_ERROR_STOP=1 -q -f "$f" >/dev/null
done

cd /srv
python -m db.seed

exec uvicorn app.api.main:app --host 0.0.0.0 --port "${PORT:-10000}"
