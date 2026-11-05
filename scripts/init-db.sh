#!/bin/sh
set -eu
# Выполняется официальным postgres image только на пустом volume.
# Форматирование %I/%L выполняется PostgreSQL, пароль не интерполируется в SQL.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=app_password="$APP_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE cardlegends_app LOGIN PASSWORD %L', :'app_password') \gexec
REVOKE ALL ON DATABASE :"DBNAME" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"DBNAME" TO cardlegends_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO cardlegends_app;
SQL
