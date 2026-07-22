from __future__ import annotations

from collections import defaultdict

import frappe
from frappe.core.doctype.rq_worker.rq_worker import serialize_worker
from frappe.desk.doctype.system_health_report.system_health_report import SystemHealthReport
from frappe.utils import get_datetime, now_datetime
from frappe.utils.background_jobs import get_workers
from frappe.utils.scheduler import get_scheduler_status, get_scheduler_tick

SCHEDULER_HEARTBEAT_KEY = "ukrainian_integrations:scheduler_heartbeat"
MINIMUM_HEARTBEAT_WINDOW_SECONDS = 15 * 60


def _heartbeat_window_seconds() -> int:
    return max(int(get_scheduler_tick()) * 3, MINIMUM_HEARTBEAT_WINDOW_SECONDS)


def update_scheduler_heartbeat() -> None:
    """Record an end-to-end scheduler/worker heartbeat in the shared Redis cache."""
    window = _heartbeat_window_seconds()
    frappe.cache.set_value(
        SCHEDULER_HEARTBEAT_KEY,
        now_datetime().isoformat(),
        expires_in_sec=window * 2,
    )


def _has_recent_scheduler_heartbeat() -> bool:
    value = frappe.cache.get_value(SCHEDULER_HEARTBEAT_KEY)
    if not value:
        return False

    try:
        age = (now_datetime() - get_datetime(value)).total_seconds()
    except (TypeError, ValueError):
        return False
    return 0 <= age <= _heartbeat_window_seconds()


class ContainerAwareSystemHealthReport(SystemHealthReport):
    """Correct health-report false negatives caused by isolated Docker filesystems."""

    def fetch_background_jobs(self):
        super().fetch_background_jobs()
        if self.total_background_workers:
            return

        # RQ 2.6 workers running as PID 1 in containers can have no persisted
        # ``pid`` field. Frappe's virtual RQ Worker list filters them out even
        # though Worker.all() returns live workers with fresh heartbeats.
        try:
            workers = get_workers()
            serialized_workers = [serialize_worker(worker) for worker in workers]
        except Exception:
            frappe.log(frappe.get_traceback())
            return

        if not serialized_workers:
            return

        self.total_background_workers = len(serialized_workers)
        self.set("background_workers", [])
        queue_summary = defaultdict(list)
        for worker in serialized_workers:
            queue_summary[worker.queue_type].append(worker)

        for queue_type, queue_workers in queue_summary.items():
            self.append(
                "background_workers",
                {
                    "count": len(queue_workers),
                    "queues": queue_type,
                    "failed_jobs": sum(worker.failed_job_count or 0 for worker in queue_workers),
                    "utilization": sum(
                        worker.utilization_percent or 0 for worker in queue_workers
                    )
                    / len(queue_workers),
                },
            )

    def fetch_scheduler(self):
        super().fetch_scheduler()
        scheduler_enabled = get_scheduler_status().get("status") == "active"
        if (
            self.scheduler_status == "Process Not Found"
            and scheduler_enabled
            and _has_recent_scheduler_heartbeat()
        ):
            self.scheduler_status = "Active"
