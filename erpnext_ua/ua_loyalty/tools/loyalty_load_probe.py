"""Concurrent quote/reserve probe for a prepared non-production POS order set.

Run outside bench with LOYALTY_API_KEY and LOYALTY_API_SECRET environment
variables. Never point this utility at a production checkout without an
approved test scope and disposable orders.
"""

from __future__ import annotations

import argparse
import os
from concurrent.futures import ThreadPoolExecutor

import requests


def reserve_once(base_url: str, session_token: str, order: str, number: int) -> tuple[int, dict]:
    headers = {"Authorization": f"token {os.environ['LOYALTY_API_KEY']}:{os.environ['LOYALTY_API_SECRET']}"}
    quote = requests.post(
        f"{base_url}/api/method/erpnext_ua.ua_loyalty.api.quote",
        headers=headers,
        data={"pos_session_token": session_token, "source_name": order, "requested_redemption": "1.00"},
        timeout=15,
    )
    quote.raise_for_status()
    payload = quote.json()["message"]
    response = requests.post(
        f"{base_url}/api/method/erpnext_ua.ua_loyalty.api.reserve",
        headers=headers,
        data={
            "pos_session_token": session_token,
            "source_name": order,
            "quote_hash": payload["quote_hash"],
            "idempotency_key": f"load-probe:{order}:{number}",
        },
        timeout=15,
    )
    return response.status_code, response.json()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("session_token")
    parser.add_argument("orders", nargs="+")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(
            pool.map(
                lambda pair: reserve_once(args.base_url.rstrip("/"), args.session_token, pair[1], pair[0]),
                enumerate(args.orders, 1),
            )
        )
    for status, payload in results:
        print(status, payload)


if __name__ == "__main__":
    main()
