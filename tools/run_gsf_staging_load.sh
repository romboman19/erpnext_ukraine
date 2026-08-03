#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 5 ]]; then
  echo "usage: $0 <backend-container> <site> <run-id> <workers> <iterations>" >&2
  exit 2
fi

container=$1
site=$2
run_id=$3
workers=$4
iterations=$5

case "$site" in
  fifoaccept.local|integration.local|postest.local) ;;
  *)
    echo "refusing non-acceptance site: $site" >&2
    exit 2
    ;;
esac

if [[ ! $run_id =~ ^[A-Za-z0-9_-]{1,40}$ ]]; then
  echo "run-id must contain only 1-40 letters, digits, underscores, or dashes" >&2
  exit 2
fi
if [[ ! $workers =~ ^[0-9]+$ ]] || ((workers < 1 || workers > 32)); then
  echo "workers must be between 1 and 32" >&2
  exit 2
fi
if [[ ! $iterations =~ ^[0-9]+$ ]] || ((iterations < 1 || iterations > 1000)); then
  echo "iterations must be between 1 and 1000" >&2
  exit 2
fi

module=erpnext_ua.group_stock_fifo.integration_tests.phase_8_load_worker
docker exec "$container" bench --site "$site" execute "$module.prepare" \
  --kwargs "{\"confirm_write\":\"PREPARE_GSF_PHASE_8_LOAD\",\"run_id\":\"$run_id\",\"expected_workers\":$workers}"

docker exec "$container" bench --site "$site" execute "$module.enqueue_expiry_probe" \
  --kwargs "{\"run_id\":\"$run_id\"}"
docker exec "$container" bench --site "$site" execute "$module.wait_for_expiry_probe" \
  --kwargs "{\"run_id\":\"$run_id\",\"timeout_seconds\":45}"

failure_ready="/tmp/gsf-failure-${run_id}.ready"
failure_log="/tmp/gsf-failure-${run_id}.log"
failure_exec_pid=""
failure_os_pid=""

cleanup_failure_worker() {
  if [[ $failure_os_pid =~ ^[0-9]+$ ]]; then
    docker exec "$container" kill -9 "$failure_os_pid" >/dev/null 2>&1 || true
  fi
  if [[ $failure_exec_pid =~ ^[0-9]+$ ]]; then
    kill "$failure_exec_pid" >/dev/null 2>&1 || true
  fi
}
trap cleanup_failure_worker EXIT

docker exec \
  -e GSF_LOAD_RUN_ID="$run_id" \
  "$container" bench --site "$site" execute "$module.crash_after_reserve" \
  >"$failure_log" 2>&1 &
failure_exec_pid=$!

for ((attempt = 1; attempt <= 30; attempt++)); do
  if docker exec "$container" test -f "$failure_ready"; then
    break
  fi
  sleep 1
done
if ! docker exec "$container" test -f "$failure_ready"; then
  sed -n '1,240p' "$failure_log" >&2
  echo "failure-injection worker did not reach the reserved state" >&2
  exit 1
fi

failure_os_pid=$(docker exec "$container" python -c \
  'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["pid"])' \
  "$failure_ready")
if [[ ! $failure_os_pid =~ ^[0-9]+$ ]]; then
  echo "failure-injection worker published an invalid pid" >&2
  exit 1
fi
docker exec "$container" kill -9 "$failure_os_pid"
if wait "$failure_exec_pid"; then
  echo "failure-injection worker exited successfully instead of being killed" >&2
  exit 1
fi
failure_exec_pid=""
failure_os_pid=""

docker exec "$container" bench --site "$site" execute "$module.verify_crash_recovery" \
  --kwargs "{\"run_id\":\"$run_id\"}"

start_at=$(($(date +%s) + 8))
pids=()
logs=()
for ((worker = 1; worker <= workers; worker++)); do
  log="/tmp/gsf-load-${run_id}-${worker}.log"
  logs+=("$log")
  docker exec \
    -e GSF_LOAD_RUN_ID="$run_id" \
    -e GSF_LOAD_WORKER="$worker" \
    -e GSF_LOAD_ITERATIONS="$iterations" \
    -e GSF_LOAD_START="$start_at" \
    "$container" bench --site "$site" execute "$module.run" >"$log" 2>&1 &
  pids+=("$!")
done

failed=0
for pid in "${pids[@]}"; do
  if ! wait "$pid"; then
    failed=1
  fi
done
if ((failed)); then
  for log in "${logs[@]}"; do
    echo "--- $log" >&2
    sed -n '1,240p' "$log" >&2
  done
  exit 1
fi

docker exec "$container" bench --site "$site" execute "$module.report" \
  --kwargs "{\"run_id\":\"$run_id\",\"expected_workers\":$workers,\"expected_iterations\":$iterations}"
