from __future__ import annotations

from ukrainian_integrations.ecommerce.core.orchestrator import sync_orders_all, sync_stock_all


def cron_sync_orders():
    return sync_orders_all()


def cron_sync_stock():
    return sync_stock_all()
