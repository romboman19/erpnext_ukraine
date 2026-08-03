"""Production-shaped GSF contention probe for an isolated acceptance site.

Run several copies in separate processes. Each copy repeatedly reserves and
releases one unit from the same FIFO scope, forcing all database connections
through the production row-lock path without consuming or fabricating stock.

The supported sites are enforced by the committed Phase 3 fixture. Never add a
production site to that allowlist.
"""

from __future__ import annotations

import json
import os
import re
import time
from decimal import Decimal
from pathlib import Path
from statistics import mean
from typing import Any

import frappe
from frappe.utils import get_datetime, now_datetime
from frappe.utils.background_jobs import get_workers
from frappe.utils.scheduler import is_scheduler_inactive

from erpnext_ua.integrations.monitoring.system_health import (
    SCHEDULER_HEARTBEAT_KEY,
    scheduler_heartbeat_window_seconds,
)

from ..services.allocations import release_allocation, reserve
from ..services.reservation import (
    ALLOCATION_EXPIRED,
    LIVE_ALLOCATION_STATUSES,
    ReservationRequest,
)
from .phase_3_fixture import GROUP, ITEM, LOCATION_CODE, assert_site, companies, pool_name

RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,40}\Z")
MAX_WORKERS = 32
MAX_ITERATIONS = 1_000


def prepare(
    confirm_write: str,
    run_id: str,
    expected_workers: int,
) -> dict[str, Any]:
    """Build the namespaced fixture and tune bounded retries for contention."""
    assert_site()
    if confirm_write != "PREPARE_GSF_PHASE_8_LOAD":
        raise RuntimeError("confirm_write token required")
    run_id = _run_id(run_id)
    workers = _bounded_value(expected_workers, minimum=1, maximum=MAX_WORKERS)
    _assert_run_id_unused(run_id)
    for worker in range(1, workers + 1):
        _result_path(run_id, worker).unlink(missing_ok=True)
    _failure_ready_path(run_id).unlink(missing_ok=True)
    for contestant in ("a", "b"):
        _race_result_path(run_id, contestant).unlink(missing_ok=True)

    from ..spikes.fixtures import build as build_phase_0
    from .phase_3_fixture import build as build_phase_3

    build_phase_0(frappe.local.site, "BUILD_GSF_PHASE_0")
    phase_3 = build_phase_3("BUILD_GSF_PHASE_3")
    settings = frappe.get_single("GSF Settings")
    settings.enabled = 1
    settings.allocation_retry_limit = max(int(settings.allocation_retry_limit or 0), 5)
    settings.allocation_ttl_minutes = max(int(settings.allocation_ttl_minutes or 0), 30)
    settings.save(ignore_permissions=True)
    frappe.db.commit()
    if _reserved_total() != 0 or frappe.db.count(
        "GSF Allocation", {"status": ("in", LIVE_ALLOCATION_STATUSES)}
    ):
        raise RuntimeError("Acceptance fixture has a live reservation before the run")
    return {
        "site": frappe.local.site,
        "run_id": run_id,
        "phase_3": phase_3,
        "health": health(),
    }


def health() -> dict[str, Any]:
    """Return the scheduler/worker evidence required before a load run."""
    assert_site()
    heartbeat = frappe.cache.get_value(SCHEDULER_HEARTBEAT_KEY)
    heartbeat_age = None
    if heartbeat:
        heartbeat_age = max(
            0.0,
            (now_datetime() - get_datetime(heartbeat)).total_seconds(),
        )
    return {
        "site": frappe.local.site,
        "scheduler_active": not is_scheduler_inactive(),
        "scheduler_heartbeat": heartbeat,
        "scheduler_heartbeat_age_seconds": heartbeat_age,
        "scheduler_heartbeat_window_seconds": scheduler_heartbeat_window_seconds(),
        "workers": len(get_workers()),
        "queued_reposts": frappe.db.count(
            "Repost Item Valuation",
            {"status": ("in", ("Queued", "In Progress")), "docstatus": 1},
        ),
    }


def run() -> dict[str, Any]:
    """Execute one independent load worker using environment coordinates."""
    assert_site()
    run_id = _run_id(os.environ["GSF_LOAD_RUN_ID"])
    worker = _bounded_int("GSF_LOAD_WORKER", minimum=1, maximum=MAX_WORKERS)
    iterations = _bounded_int("GSF_LOAD_ITERATIONS", minimum=1, maximum=MAX_ITERATIONS)
    start_at = float(os.environ["GSF_LOAD_START"])
    request_base = _request_coordinates()
    deadlocks_before = _innodb_deadlocks()
    latencies: list[float] = []
    errors: list[dict[str, Any]] = []

    frappe.db.commit()
    time.sleep(max(0.0, start_at - time.time()))
    began = time.monotonic()
    for iteration in range(1, iterations + 1):
        request = ReservationRequest(
            idempotency_key=f"load:{run_id}:{worker}:{iteration}",
            qty=Decimal("1"),
            **request_base,
        )
        operation_began = time.monotonic()
        phase = "reserve"
        try:
            allocation = reserve(request)
            frappe.db.commit()
            phase = "release"
            release_allocation(allocation.name, reason=f"Phase 8 load {run_id}")
            frappe.db.commit()
            latencies.append(time.monotonic() - operation_began)
        except Exception as error:  # noqa: BLE001 - evidence must retain unexpected failures
            frappe.db.rollback()
            errors.append(
                {
                    "iteration": iteration,
                    "phase": phase,
                    "type": type(error).__name__,
                    "code": getattr(error, "code", None),
                    "message": str(error)[:200],
                }
            )

    result = {
        "run_id": run_id,
        "worker": worker,
        "iterations": iterations,
        "successes": len(latencies),
        "errors": errors,
        "elapsed_seconds": round(time.monotonic() - began, 6),
        "latencies_seconds": [round(value, 6) for value in latencies],
        "innodb_deadlocks_before": deadlocks_before,
        "innodb_deadlocks_after": _innodb_deadlocks(),
    }
    _result_path(run_id, worker).write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return result


def enqueue_expiry_probe(run_id: str) -> dict[str, Any]:
    """Reserve an immediately-expired unit and enqueue the real scheduled job."""
    assert_site()
    run_id = _run_id(run_id)
    settings = frappe.get_single("GSF Settings")
    settings.allocation_ttl_minutes = 0
    settings.save(ignore_permissions=True)
    frappe.db.commit()

    request = ReservationRequest(
        idempotency_key=f"expiry:{run_id}",
        qty=Decimal("1"),
        **_request_coordinates(),
    )
    allocation = reserve(request)
    frappe.db.commit()

    settings = frappe.get_single("GSF Settings")
    settings.allocation_ttl_minutes = 30
    settings.save(ignore_permissions=True)
    frappe.enqueue(
        "erpnext_ua.group_stock_fifo.services.allocations.expire_due_allocations",
        queue="long",
        job_id=f"gsf-phase-8-expiry-{run_id}",
        enqueue_after_commit=True,
    )
    frappe.db.commit()
    return {
        "run_id": run_id,
        "allocation": allocation.name,
        "status": allocation.status,
        "expires_at": allocation.expires_at,
    }


def wait_for_expiry_probe(run_id: str, timeout_seconds: int = 45) -> dict[str, Any]:
    """Wait for the long-queue worker to execute the scheduled expiry service."""
    assert_site()
    run_id = _run_id(run_id)
    timeout = _bounded_value(timeout_seconds, minimum=1, maximum=60)
    key = f"expiry:{run_id}"
    deadline = time.monotonic() + timeout
    status = None
    while time.monotonic() < deadline:
        frappe.db.rollback()
        status = frappe.db.get_value("GSF Allocation", {"idempotency_key": key}, "status")
        if status == ALLOCATION_EXPIRED:
            break
        time.sleep(0.5)

    checks = {
        "allocation_expired_by_worker": status == ALLOCATION_EXPIRED,
        "no_reserved_qty_leaked": _reserved_total() == 0,
    }
    output = {"status": "pass" if all(checks.values()) else "fail", "checks": checks}
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    if output["status"] != "pass":
        raise RuntimeError("GSF scheduled expiry acceptance failed")
    return output


def crash_after_reserve() -> None:
    """Hold an uncommitted reservation until the harness kills this process."""
    assert_site()
    run_id = _run_id(os.environ["GSF_LOAD_RUN_ID"])
    request = ReservationRequest(
        idempotency_key=f"failure:{run_id}:crash",
        qty=Decimal("1"),
        **_request_coordinates(),
    )
    allocation = reserve(request)
    _failure_ready_path(run_id).write_text(
        json.dumps(
            {"pid": os.getpid(), "allocation": allocation.name},
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    time.sleep(300)
    raise RuntimeError("Failure-injection worker was not terminated by the harness")


def verify_crash_recovery(run_id: str) -> dict[str, Any]:
    """Prove crash rollback, released locks, and immediate scope reuse."""
    assert_site()
    run_id = _run_id(run_id)
    crash_key = f"failure:{run_id}:crash"
    crash_allocation = frappe.db.get_value(
        "GSF Allocation", {"idempotency_key": crash_key}, ["name", "status"], as_dict=True
    )
    checks = {
        "uncommitted_allocation_rolled_back": not crash_allocation,
        "no_reserved_qty_after_crash": _reserved_total() == 0,
    }

    recovery = reserve(
        ReservationRequest(
            idempotency_key=f"failure:{run_id}:recovery",
            qty=Decimal("1"),
            **_request_coordinates(),
        )
    )
    frappe.db.commit()
    release_allocation(recovery.name, reason=f"Phase 8 crash recovery {run_id}")
    frappe.db.commit()
    checks.update(
        {
            "scope_lock_reusable": True,
            "no_reserved_qty_after_recovery": _reserved_total() == 0,
            "no_live_failure_allocations": frappe.db.count(
                "GSF Allocation",
                {
                    "idempotency_key": ("like", f"failure:{run_id}:%"),
                    "status": ("in", LIVE_ALLOCATION_STATUSES),
                },
            )
            == 0,
        }
    )
    output = {"status": "pass" if all(checks.values()) else "fail", "checks": checks}
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    if output["status"] != "pass":
        raise RuntimeError("GSF crash recovery acceptance failed")
    return output


def verify_last_stock_race(run_id: str) -> dict[str, Any]:
    """Require exactly one winner when two processes reserve the full pool."""
    assert_site()
    run_id = _run_id(run_id)
    results = [_load_race_result(run_id, contestant) for contestant in ("a", "b")]
    winners = [result for result in results if result.get("won")]
    losers = [result for result in results if not result.get("won")]
    checks = {
        "exactly_one_winner": len(winners) == 1,
        "exactly_one_loser": len(losers) == 1,
        "winner_took_full_pool": (
            len(winners) == 1 and Decimal(str(winners[0].get("allocated_qty"))) == Decimal("10")
        ),
        "reserved_total_never_exceeded_stock": _reserved_total() <= Decimal("10"),
    }

    for winner in winners:
        allocation_name = winner.get("allocation")
        if allocation_name and frappe.db.get_value(
            "GSF Allocation", allocation_name, "status"
        ) in LIVE_ALLOCATION_STATUSES:
            release_allocation(allocation_name, reason=f"Phase 8 last-stock race {run_id}")
            frappe.db.commit()
    checks.update(
        {
            "no_reserved_qty_after_race": _reserved_total() == 0,
            "no_live_race_allocations": frappe.db.count(
                "GSF Allocation",
                {
                    "idempotency_key": ("like", f"race:{run_id}:%"),
                    "status": ("in", LIVE_ALLOCATION_STATUSES),
                },
            )
            == 0,
        }
    )
    output = {
        "status": "pass" if all(checks.values()) else "fail",
        "results": results,
        "checks": checks,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    if output["status"] != "pass":
        raise RuntimeError("GSF last-stock race acceptance failed")
    return output


def cleanup_failed_run(confirm_write: str, run_id: str) -> dict[str, Any]:
    """Release live load evidence after a failed acceptance attempt."""
    assert_site()
    if confirm_write != "RELEASE_FAILED_GSF_PHASE_8_RUN":
        raise RuntimeError("confirm_write token required")
    run_id = _run_id(run_id)
    released = []
    for name in frappe.get_all(
        "GSF Allocation",
        {
            "idempotency_key": ("like", f"load:{run_id}:%"),
            "status": ("in", LIVE_ALLOCATION_STATUSES),
        },
        pluck="name",
    ):
        release_allocation(name, reason=f"Failed Phase 8 acceptance cleanup {run_id}")
        frappe.db.commit()
        released.append(name)
    reserved_total = _reserved_total()
    output = {
        "run_id": run_id,
        "released": released,
        "reserved_qty_after": str(reserved_total),
        "live_allocations_after": frappe.db.count(
            "GSF Allocation",
            {
                "idempotency_key": ("like", f"load:{run_id}:%"),
                "status": ("in", LIVE_ALLOCATION_STATUSES),
            },
        ),
    }
    if reserved_total != 0 or output["live_allocations_after"]:
        raise RuntimeError("Failed Phase 8 run cleanup left a live reservation")
    return output


def report(
    run_id: str,
    expected_workers: int,
    expected_iterations: int,
    max_p95_seconds: float = 5.0,
) -> dict[str, Any]:
    """Aggregate worker files and fail closed on any leaked reservation."""
    assert_site()
    run_id = _run_id(run_id)
    workers = _bounded_value(expected_workers, minimum=1, maximum=MAX_WORKERS)
    iterations = _bounded_value(expected_iterations, minimum=1, maximum=MAX_ITERATIONS)
    results = [_load_result(run_id, worker) for worker in range(1, workers + 1)]
    latencies = sorted(
        latency for result in results for latency in result["latencies_seconds"]
    )
    errors = [error for result in results for error in result["errors"]]
    expected_operations = workers * iterations
    successes = sum(result["successes"] for result in results)
    reserved_total = _reserved_total()
    live_allocations = frappe.db.count(
        "GSF Allocation",
        {
            "idempotency_key": ("like", f"load:{run_id}:%"),
            "status": ("in", LIVE_ALLOCATION_STATUSES),
        },
    )
    p95 = _percentile(latencies, 0.95)
    elapsed = max((result["elapsed_seconds"] for result in results), default=0.0)
    health_snapshot = health()
    checks = {
        "all_workers_reported": len(results) == workers,
        "all_operations_succeeded": successes == expected_operations and not errors,
        "no_reserved_qty_leaked": reserved_total == 0,
        "no_live_allocations_leaked": live_allocations == 0,
        "no_queued_reposts": health_snapshot["queued_reposts"] == 0,
        "p95_within_gate": p95 is not None and p95 <= float(max_p95_seconds),
        "scheduler_active": health_snapshot["scheduler_active"],
        "scheduler_heartbeat_recent": (
            health_snapshot["scheduler_heartbeat_age_seconds"] is not None
            and health_snapshot["scheduler_heartbeat_age_seconds"]
            <= health_snapshot["scheduler_heartbeat_window_seconds"]
        ),
        "worker_online": health_snapshot["workers"] >= 1,
    }
    output = {
        "status": "pass" if all(checks.values()) else "fail",
        "run_id": run_id,
        "workers": workers,
        "iterations_per_worker": iterations,
        "expected_operations": expected_operations,
        "successes": successes,
        "errors": errors,
        "elapsed_seconds": elapsed,
        "throughput_operations_per_second": round(successes / elapsed, 3) if elapsed else 0,
        "latency_seconds": {
            "mean": round(mean(latencies), 6) if latencies else None,
            "p95": p95,
            "max": max(latencies, default=None),
            "gate_p95": float(max_p95_seconds),
        },
        "innodb_deadlocks_observed": max(
            (result["innodb_deadlocks_after"] for result in results), default=0
        )
        - min((result["innodb_deadlocks_before"] for result in results), default=0),
        "reserved_qty_after": str(reserved_total),
        "live_allocations_after": live_allocations,
        "checks": checks,
        "health": health_snapshot,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    if output["status"] != "pass":
        raise RuntimeError("GSF Phase 8 load acceptance failed")
    return output


def _request_coordinates() -> dict[str, Any]:
    firms = companies()
    location = frappe.db.get_value(
        "GSF Physical Location", {"location_code": LOCATION_CODE}, "name"
    )
    if not location:
        raise RuntimeError("Phase 3 fixture is not built")
    return {
        "company_group": GROUP,
        "physical_location": location,
        "seller_company": firms[2],
        "item_code": ITEM,
        "allowed_warehouses": frozenset(pool_name(company) for company in firms),
    }


def _innodb_deadlocks() -> int:
    rows = frappe.db.sql("show global status like 'Innodb_deadlocks'")
    return int(rows[0][1]) if rows else 0


def _reserved_total() -> Decimal:
    return Decimal(
        str(
            frappe.db.sql(
                "select coalesce(sum(reserved_qty_cache), 0) from `tabGSF Layer Balance`"
            )[0][0]
        )
    )


def _assert_run_id_unused(run_id: str) -> None:
    for prefix in ("load", "failure", "expiry", "race"):
        if frappe.db.exists(
            "GSF Allocation", {"idempotency_key": ("like", f"{prefix}:{run_id}%")}
        ):
            raise RuntimeError(f"run_id {run_id} already has {prefix} allocation evidence")


def _run_id(value: str) -> str:
    if not RUN_ID_PATTERN.fullmatch(value or ""):
        raise RuntimeError("run_id must contain only 1-40 letters, digits, underscores, or dashes")
    return value


def _bounded_int(name: str, *, minimum: int, maximum: int) -> int:
    return _bounded_value(os.environ[name], minimum=minimum, maximum=maximum)


def _bounded_value(value: Any, *, minimum: int, maximum: int) -> int:
    parsed = int(value)
    if parsed < minimum or parsed > maximum:
        raise RuntimeError(f"value must be between {minimum} and {maximum}")
    return parsed


def _result_path(run_id: str, worker: int) -> Path:
    return Path("/tmp") / f"gsf-load-{run_id}-{worker}.json"


def _failure_ready_path(run_id: str) -> Path:
    return Path("/tmp") / f"gsf-failure-{run_id}.ready"


def _race_key(run_id: str, contestant: str) -> str:
    return f"race:{run_id}:{contestant}"


def _race_result_path(run_id: str, contestant: str) -> Path:
    return Path("/tmp") / f"gsf-race-{_race_key(run_id, contestant)}.json"


def _load_race_result(run_id: str, contestant: str) -> dict[str, Any]:
    path = _race_result_path(run_id, contestant)
    if not path.is_file():
        raise RuntimeError(f"Missing last-stock race result {contestant}")
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("key") != _race_key(run_id, contestant):
        raise RuntimeError(f"Invalid last-stock race coordinates {contestant}")
    return result


def _load_result(run_id: str, worker: int) -> dict[str, Any]:
    path = _result_path(run_id, worker)
    if not path.is_file():
        raise RuntimeError(f"Missing result from load worker {worker}")
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("run_id") != run_id or result.get("worker") != worker:
        raise RuntimeError(f"Invalid result coordinates from load worker {worker}")
    return result


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    index = max(0, min(len(values) - 1, round((len(values) - 1) * percentile)))
    return round(values[index], 6)
