#!/usr/bin/env bash
set -euo pipefail

BENCH_DIR=/home/frappe/frappe-bench
APP_DIR="$BENCH_DIR/apps/ukrainian_integrations"
SOURCE_DIR="${GITHUB_WORKSPACE:-$(pwd)}"

if [[ -e "$APP_DIR" ]]; then
  echo "App directory already exists: $APP_DIR" >&2
  exit 1
fi

cp -a "$SOURCE_DIR" "$APP_DIR"
chown -R frappe:frappe "$APP_DIR"

runuser -u frappe -- bash -lc "cd '$BENCH_DIR' && ./env/bin/pip install -e apps/ukrainian_integrations"
runuser -u frappe -- bash -lc "cd '$BENCH_DIR' && echo >> sites/apps.txt && echo ukrainian_integrations >> sites/apps.txt"
runuser -u frappe -- bash -lc "cd '$BENCH_DIR' && bench set-config -g db_host mariadb"
runuser -u frappe -- bash -lc "cd '$BENCH_DIR' && bench set-config -g redis_cache redis://redis:6379"
runuser -u frappe -- bash -lc "cd '$BENCH_DIR' && bench set-config -g redis_queue redis://redis:6379"
runuser -u frappe -- bash -lc "cd '$BENCH_DIR' && bench set-config -g redis_socketio redis://redis:6379"
runuser -u frappe -- bash -lc "cd '$BENCH_DIR' && bench new-site ci.localhost --db-root-username root --mariadb-root-password admin --admin-password admin --mariadb-user-host-login-scope='%'"
runuser -u frappe -- bash -lc "cd '$BENCH_DIR' && bench --site ci.localhost install-app erpnext"
runuser -u frappe -- bash -lc "cd '$BENCH_DIR' && bench --site ci.localhost install-app ukrainian_integrations"
runuser -u frappe -- bash -lc "cd '$BENCH_DIR' && bench --site ci.localhost migrate"
runuser -u frappe -- bash -lc "cd '$BENCH_DIR' && bench --site ci.localhost execute ukrainian_integrations.diagnostics.run_installation_checks"
runuser -u frappe -- bash -lc "cd '$BENCH_DIR' && bench --site ci.localhost set-config allow_tests true"
runuser -u frappe -- bash -lc "cd '$BENCH_DIR' && bench --site ci.localhost run-tests --app ukrainian_integrations"
runuser -u frappe -- bash -lc "cd '$BENCH_DIR' && bench --site ci.localhost list-apps"
