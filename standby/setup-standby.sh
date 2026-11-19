#!/bin/sh
# Standby серверді дайындау:
# алғаш іске қосылғанда pg_basebackup арқылы primary көшірмесін алады,
# кейін hot standby режимінде WAL ағынын үздіксіз қабылдайды.
set -e

# Alpine образында root құқығынан postgres қолданушысына өту командасы
if command -v gosu >/dev/null 2>&1; then RUN_AS="gosu postgres"; else RUN_AS="su-exec postgres"; fi

if [ ! -s "$PGDATA/PG_VERSION" ]; then
  mkdir -p "$PGDATA" && chown -R postgres:postgres "$PGDATA"

  until pg_isready -h postgres_primary -U "$POSTGRES_USER" -d "$POSTGRES_DB"; do
    echo "primary күтілуде..."; sleep 2
  done

  export PGPASSWORD="$REPLICATION_PASSWORD"
  # -Xs: WAL ағынымен бірге көшіру, -R: standby.signal және primary_conninfo жасау
  $RUN_AS pg_basebackup -h postgres_primary -D "$PGDATA" -U replicator -Fp -Xs -P -R -c fast

  echo "primary_conninfo = 'host=postgres_primary user=replicator password=$REPLICATION_PASSWORD'" \
       >> "$PGDATA/postgresql.auto.conf"
  chmod 700 "$PGDATA"
fi

exec docker-entrypoint.sh postgres -c hot_standby=on
