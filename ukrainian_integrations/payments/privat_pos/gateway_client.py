from __future__ import annotations

import requests


class PrivatPOSGatewayClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 20):
        self.base_url = (base_url or '').rstrip('/')
        self.api_key = (api_key or '').strip()
        self.timeout = int(timeout or 20)
        if not self.base_url:
            raise ValueError('PB POS gateway URL is required')
        if not self.api_key:
            raise ValueError('PB POS gateway API key is required')

    def _post_v1(self, payload: dict) -> dict:
        headers = {'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json'}
        r = requests.post(f'{self.base_url}/v1/pos/operation', json=payload, headers=headers, timeout=self.timeout)
        r.raise_for_status()
        return r.json() if (r.text or '').strip() else {'ok': False, 'error': 'empty_response'}

    def _post_legacy(self, path: str, terminal_ip: str, params: dict | None = None) -> dict:
        headers = {'X-API-Key': self.api_key, 'Content-Type': 'application/json'}
        payload = {'terminal': terminal_ip, 'params': params or {}}
        r = requests.post(f'{self.base_url}{path}', json=payload, headers=headers, timeout=self.timeout)
        body = (r.text or '').strip()
        try:
            data = r.json() if body else {'ok': False, 'error': 'empty_response'}
        except Exception:
            data = {'ok': False, 'error': True, 'description': body or f'HTTP {r.status_code}'}
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

        # Legacy-first (matches existing n8n flows in privatbank-pos-http-connector)
        try:
            if op == 'sale':
                try:
                    return self._post_legacy('/purchase', terminal_ip=terminal_ip, params={'amount': float(amount)})
                except requests.RequestException as e:
                    msg = str(e)
                    if 'таймаут відправки запиту до горутини' in msg.lower() or 'timeout' in msg.lower():
                        import time
                        time.sleep(2)
                        return self._post_legacy('/purchase', terminal_ip=terminal_ip, params={'amount': float(amount)})
                    raise
            if op == 'refund':
                invoice = (extra or {}).get('reference_operation_id') if extra else None
                if not invoice:
                    raise ValueError('reference_operation_id is required for legacy refund endpoint')
                return self._post_legacy('/refund', terminal_ip=terminal_ip, params={'amount': float(amount), 'invoiceNumber': str(invoice)})
        except requests.RequestException:
            pass

        # Optional v1 fallback
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
