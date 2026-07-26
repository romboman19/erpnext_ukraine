"""Test-site-only Inventory Dimension balance-query benchmark."""

from __future__ import annotations

from statistics import median
from time import perf_counter
from typing import Any

from .inventory_dimension import DIMENSION_FIELD, _assert_test_scope

TEMP_TABLE = "tmp_tp_dimension_balance"
ROW_COUNT = 250_000
ITERATIONS = 30


def _rows_as_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _explain(frappe: Any, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    return _rows_as_dicts(frappe.db.sql(f"EXPLAIN {query}", params, as_dict=True))


def _benchmark(frappe: Any, query: str, params: tuple[Any, ...]) -> dict[str, Any]:
    for _ in range(3):
        frappe.db.sql(query, params)

    samples_ms = []
    value = None
    for _ in range(ITERATIONS):
        started = perf_counter()
        value = frappe.db.sql(query, params)[0][0]
        samples_ms.append((perf_counter() - started) * 1000)

    ordered = sorted(samples_ms)
    p95_index = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return {
        "iterations": ITERATIONS,
        "value": float(value or 0),
        "min_ms": round(ordered[0], 4),
        "median_ms": round(median(ordered), 4),
        "p95_ms": round(ordered[p95_index], 4),
        "max_ms": round(ordered[-1], 4),
    }


def _actual_sle_indexes(frappe: Any) -> list[dict[str, Any]]:
    rows = frappe.db.sql("SHOW INDEX FROM `tabStock Ledger Entry`", as_dict=True)
    relevant_columns = {"item_code", "warehouse", DIMENSION_FIELD, "is_cancelled"}
    relevant_keys = {
        row.Key_name for row in rows if row.Column_name in relevant_columns
    }
    return [
        {
            "key_name": row.Key_name,
            "sequence": int(row.Seq_in_index),
            "column": row.Column_name,
            "non_unique": int(row.Non_unique),
            "cardinality": int(row.Cardinality or 0),
        }
        for row in rows
        if row.Key_name in relevant_keys
    ]


def _drop_temporary_table(frappe: Any, table: str) -> None:
    # Frappe blocks DDL after writes to guard against implicit commits. A commit
    # here only closes the temporary-table benchmark transaction; temporary
    # tables remain scoped to this connection and are then explicitly dropped.
    frappe.db.commit()
    frappe.db.sql(f"DROP TEMPORARY TABLE IF EXISTS {table}")


def _create_temporary_table(frappe: Any, table: str, *, composite: bool) -> None:
    composite_index = (
        ", INDEX idx_balance_lookup (item_code, warehouse, owner_lot, is_cancelled)"
        if composite
        else ""
    )
    frappe.db.sql(
        f"""
        CREATE TEMPORARY TABLE {table} (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            item_code VARCHAR(140) NOT NULL,
            warehouse VARCHAR(140) NOT NULL,
            owner_lot VARCHAR(140) NOT NULL,
            is_cancelled TINYINT(1) NOT NULL DEFAULT 0,
            actual_qty DECIMAL(21,9) NOT NULL,
            PRIMARY KEY (id),
            INDEX idx_owner_lot (owner_lot)
            {composite_index}
        ) ENGINE=InnoDB
        """
    )


def _populate_temporary_table(frappe: Any, table: str) -> None:
    frappe.db.sql(
        f"""
        INSERT INTO {table} (item_code, warehouse, owner_lot, is_cancelled, actual_qty)
        SELECT
            CONCAT('ITEM-', LPAD(MOD(seq, 1000), 4, '0')),
            CONCAT('WH-', LPAD(MOD(FLOOR(seq / 1000), 10), 2, '0')),
            CONCAT('OWNER-', LPAD(MOD(FLOOR(seq / 10000), 25), 3, '0')),
            IF(MOD(seq, 20) = 0, 1, 0),
            IF(MOD(seq, 3) = 0, -1, 1)
        FROM seq_1_to_{ROW_COUNT}
        """
    )
    frappe.db.sql(f"ANALYZE TABLE {table}")


def run_dimension_balance_benchmark(
    confirm_site: str,
    confirm_write: str,
    company: str = "POS Test Ukraine",
) -> dict[str, Any]:
    """Benchmark the exact dimension-balance aggregate query shape."""
    import frappe

    _assert_test_scope(
        frappe,
        confirm_site=confirm_site,
        confirm_write=confirm_write,
        company=company,
    )
    frappe.set_user("Administrator")

    actual_query = f"""
        SELECT COALESCE(SUM(actual_qty), 0)
        FROM `tabStock Ledger Entry`
        WHERE item_code = %s
          AND warehouse = %s
          AND `{DIMENSION_FIELD}` = %s
          AND is_cancelled = 0
    """
    actual_params = (
        "TP-GATE-0B-ZERO-VALUE-ITEM",
        "TP Gate 0B Warehouse - PTU",
        "TP-GATE-0B-LOT-001",
    )
    result: dict[str, Any] = {
        "site": frappe.local.site,
        "company": company,
        "row_count": ROW_COUNT,
        "iterations": ITERATIONS,
        "actual_sle_count": int(frappe.db.sql("SELECT COUNT(*) FROM `tabStock Ledger Entry`")[0][0]),
        "actual_dimension_sle_count": int(
            frappe.db.sql(
                f"SELECT COUNT(*) FROM `tabStock Ledger Entry` WHERE `{DIMENSION_FIELD}` IS NOT NULL"
            )[0][0]
        ),
        "actual_sle_indexes": _actual_sle_indexes(frappe),
        "actual_query_explain": _explain(frappe, actual_query, actual_params),
        "actual_query_timing": _benchmark(frappe, actual_query, actual_params),
    }

    table = f"`{TEMP_TABLE}`"
    benchmark_query = f"""
        SELECT COALESCE(SUM(actual_qty), 0)
        FROM {table}
        WHERE item_code = %s
          AND warehouse = %s
          AND owner_lot = %s
          AND is_cancelled = 0
    """
    benchmark_params = ("ITEM-0001", "WH-00", "OWNER-000")

    table_removed = False
    frappe.db.sql(f"DROP TEMPORARY TABLE IF EXISTS {table}")
    try:
        _create_temporary_table(frappe, table, composite=False)
        _populate_temporary_table(frappe, table)

        result["single_index"] = {
            "explain": _explain(frappe, benchmark_query, benchmark_params),
            "timing": _benchmark(frappe, benchmark_query, benchmark_params),
        }

        _drop_temporary_table(frappe, table)
        _create_temporary_table(frappe, table, composite=True)
        _populate_temporary_table(frappe, table)
        result["composite_index"] = {
            "explain": _explain(frappe, benchmark_query, benchmark_params),
            "timing": _benchmark(frappe, benchmark_query, benchmark_params),
        }
    finally:
        _drop_temporary_table(frappe, table)
        table_removed = True
        result["temporary_table_removed"] = table_removed

    single_rows = int(result["single_index"]["explain"][0]["rows"] or 0)
    composite_rows = int(result["composite_index"]["explain"][0]["rows"] or 0)
    result["rows_examined_reduction"] = (
        round(single_rows / composite_rows, 2) if composite_rows else None
    )
    result["median_speedup"] = round(
        result["single_index"]["timing"]["median_ms"]
        / result["composite_index"]["timing"]["median_ms"],
        2,
    )

    if not result["temporary_table_removed"]:
        raise AssertionError(f"Expected temporary benchmark table to be removed: {result}")
    if not composite_rows or composite_rows >= single_rows:
        raise AssertionError(f"Expected composite index to reduce estimated rows: {result}")

    return result
