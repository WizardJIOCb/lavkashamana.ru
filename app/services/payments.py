import uuid
from decimal import Decimal
import httpx
from fastapi import HTTPException
from ..config import settings

class PaymentGateway:
    async def create(self, order_id: int, amount: Decimal, description: str, receipt: dict | None = None) -> dict:
        raise NotImplementedError
    async def fetch(self, payment_id: str) -> dict:
        raise NotImplementedError

class MockGateway(PaymentGateway):
    async def create(self, order_id: int, amount: Decimal, description: str, receipt: dict | None = None) -> dict:
        pid = f'mock_{uuid.uuid4().hex}'
        return {'id': pid, 'status': 'pending', 'confirmation_url': f'{settings.app_url}/api/payments/mock/{pid}/success?order_id={order_id}'}
    async def fetch(self, payment_id: str) -> dict:
        return {'id': payment_id, 'status': 'succeeded', 'paid': True}

class YooKassaGateway(PaymentGateway):
    base = 'https://api.yookassa.ru/v3'
    def configured(self):
        return bool(settings.yookassa_shop_id and settings.yookassa_secret_key)
    async def create(self, order_id: int, amount: Decimal, description: str, receipt: dict | None = None) -> dict:
        if not self.configured():
            raise HTTPException(503, 'YooKassa credentials are not configured')
        headers = {'Idempotence-Key': str(uuid.uuid4()), 'Content-Type': 'application/json'}
        payload = {
            'amount': {'value': f'{amount:.2f}', 'currency': 'RUB'},
            'capture': True,
            'confirmation': {'type': 'redirect', 'return_url': settings.yookassa_return_url},
            'description': description,
            'metadata': {'order_id': str(order_id)},
        }
        if receipt:
            payload['receipt'] = receipt
        async with httpx.AsyncClient(timeout=25, auth=(settings.yookassa_shop_id, settings.yookassa_secret_key)) as client:
            r = await client.post(f'{self.base}/payments', json=payload, headers=headers)
            if r.status_code >= 400:
                raise HTTPException(r.status_code, f'YooKassa: {r.text[:500]}')
            data = r.json()
            return {'id': data['id'], 'status': data['status'], 'confirmation_url': data.get('confirmation', {}).get('confirmation_url')}
    async def fetch(self, payment_id: str) -> dict:
        async with httpx.AsyncClient(timeout=25, auth=(settings.yookassa_shop_id, settings.yookassa_secret_key)) as client:
            r = await client.get(f'{self.base}/payments/{payment_id}')
            if r.status_code >= 400:
                raise HTTPException(r.status_code, f'YooKassa: {r.text[:500]}')
            return r.json()

def get_gateway():
    return YooKassaGateway() if settings.payment_provider.lower() == 'yookassa' else MockGateway()
