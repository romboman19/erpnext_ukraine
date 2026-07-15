from __future__ import annotations

import requests


class PrivatPOSGatewayClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 60, protocol: str = 'legacy'):
        self.base_url = (base_url or '').rstrip('/')
        self.api_key = (api_key or '').strip()
        self.timeout = int(timeout or 20)
        self.protocol = (protocol or 'legacy').strip().lower()
        if not self.base_url:
            raise ValueError('PB POS gateway URL is required')
        if not self.api_key:
            raise ValueError('PB POS gateway API key is required')
        if self.protocol not in {'legacy', 'v1'}:
            raise ValueError('PB POS protocol must be legacy or v1')

    def _post_v1(self, payload: dict) -> dict:
        headers = {'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json'}
        r = requests.post(f'{self.base_url}/v1/pos/operation', json=payload, headers=headers, timeout=self.timeout)
        r.raise_for_status()
        return r.json() if (r.text or '').strip() else {}

    def _post_legacy(self, path: str, terminal_ip: str, params: dict | None = None) -> dict:
        headers = {'X-API-Key': self.api_key, 'Content-Type': 'application/json'}
        payload = {'terminal': terminal_ip, 'params': params or {}}
        r = requests.post(f'{self.base_url}{path}', json=payload, headers=headers, timeout=self.timeout)
        if r.status_code >= 500:
            # A terminal may have completed the operation before the gateway
            # failed. Treat server errors as ambiguous, never as safe rejects.
            r.raise_for_status()
        body = (r.text or '').strip()
        data = r.json() if body else {}
        if r.status_code >= 400:
            if isinstance(data, dict):
                data.setdefault('error', True)
                data.setdefault('description', f'HTTP {r.status_code}')
                return data
            return {'ok': False, 'error': True, 'description': f'HTTP {r.status_code}'}
        return data

    def operation(
        self,
        operation: str,
        terminal_ip: str,
        amount: float,
        operation_id: str,
        *,
        port: int = 2000,
        currency: str = 'UAH',
        extra: dict | None = None,
    ) -> dict:
        payload = {
            'operation': operation,
            'terminal_ip': terminal_ip,
            'terminal_port': int(port or 2000),
            'amount': float(amount),
            'currency': currency,
            'operation_id': operation_id,
        }
        if extra:
            payload.update(extra)

        op = (operation or '').lower()

        if self.protocol == 'legacy':
            if op == 'sale':
                # Never retry a financial operation after a transport error. Its outcome is unknown.
                return self._post_legacy('/purchase', terminal_ip=terminal_ip, params={'amount': float(amount)})
            if op == 'refund':
                invoice = (extra or {}).get('reference_operation_id') if extra else None
                if not invoice:
                    raise ValueError('reference_operation_id is required for legacy refund endpoint')
                return self._post_legacy('/refund', terminal_ip=terminal_ip, params={'amount': float(amount), 'invoiceNumber': str(invoice)})
            raise ValueError(f'Unsupported legacy PB POS operation: {operation}')

        # The protocol is explicit: automatic fallback can duplicate a payment after a lost response.
        return self._post_v1(payload)

    def sale(self, terminal_ip: str, amount: float, operation_id: str, *, port: int = 2000, currency: str = 'UAH') -> dict:
        return self.operation(
            operation='sale',
            terminal_ip=terminal_ip,
            port=port,
            amount=amount,
            operation_id=operation_id,
            currency=currency,
        )

    def refund(
        self,
        terminal_ip: str,
        amount: float,
        operation_id: str,
        *,
        port: int = 2000,
        currency: str = 'UAH',
        reference_operation_id: str | None = None,
    ) -> dict:
        extra = {'reference_operation_id': reference_operation_id} if reference_operation_id else None
        return self.operation(
            operation='refund',
            terminal_ip=terminal_ip,
            port=port,
            amount=amount,
            operation_id=operation_id,
            currency=currency,
            extra=extra,
        )

    def ping(self, terminal_ip: str | None = None) -> dict:
        # Legacy gateway has no /health; for real terminal connectivity use /verify
        if terminal_ip:
            return self._post_legacy('/verify', terminal_ip=terminal_ip, params={})

        # fallback for potential v1 deployments
        headers = {'Authorization': f'Bearer {self.api_key}'}
        r = requests.get(f'{self.base_url}/health', headers=headers, timeout=self.timeout)
        r.raise_for_status()
        return r.json() if (r.text or '').strip() else {'ok': True}
