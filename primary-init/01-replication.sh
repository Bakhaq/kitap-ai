#!/bin/sh
# Primary алғаш іске қосылғанда бір рет орындалады.
# 1) replicator рөлін жасайды  2) pg_hba.conf файлына репликация рұқсатын қосады
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v repl_pass="$REPLICATION_PASSWORD" <<'SQL'
CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD :'repl_pass';
SQL

echo "host replication replicator 0.0.0.0/0 scram-sha-256" >> "$PGDATA/pg_hba.conf"
