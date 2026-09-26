import time
import httpx
from fastapi import HTTPException
from ..config import settings

class CDEKClient:
    def __init__(self):
        self._token = None
        self._expires = 0

    @property
    def configured(self):
        return bool(settings.cdek_client_id and settings.cdek_client_secret)

    async def token(self):
        if not self.configured:
            raise HTTPException(503, 'CDEK credentials are not configured')
        if self._token and time.time() < self._expires - 60:
            return self._token
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                f'{settings.cdek_api_base}/v2/oauth/token',
                data={
                    'grant_type': 'client_credentials',
                    'client_id': settings.cdek_client_id,
                    'client_secret': settings.cdek_client_secret,
                },
            )
            r.raise_for_status()
            data = r.json()
            self._token = data['access_token']
            self._expires = time.time() + int(data.get('expires_in', 3600))
            return self._token

    async def _request(self, method, path, **kwargs):
        token = await self.token()
        headers = kwargs.pop('headers', {})
        headers['Authorization'] = f'Bearer {token}'
        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.request(method, f'{settings.cdek_api_base}{path}', headers=headers, **kwargs)
            if r.status_code >= 400:
                raise HTTPException(r.status_code, f'CDEK: {r.text[:500]}')
            return r.json()

    async def cities(self, query: str):
        return await self._request('GET', '/v2/location/cities', params={'city': query, 'country_codes': 'RU', 'size': 20})

    async def suggest_cities(self, query: str):
        rows = await self._request(
            'GET',
            '/v2/location/suggest/cities',
            params={
                'name': query,
                'country_code': 'RU',
            }
        )
        return rows if isinstance(rows, list) else []

    async def delivery_points(self, city_code: int):
        return await self._request('GET', '/v2/deliverypoints', params={'city_code': city_code, 'type': 'ALL'})

    async def find_origin(self):
        cities = await self.cities(settings.cdek_from_city)
        if not cities:
            raise HTTPException(500, 'CDEK origin city not found')
        # Prefer Bashkortostan when several Oktyabrsky cities exist.
        for c in cities:
            region = (c.get('region') or '').lower()
            if 'башкортостан' in region:
                return c
        return cities[0]

    async def tariff_list(self, to_city_code: int, packages: list[dict], delivery_point: str | None = None):
        origin = await self.find_origin()
        payload = {
            'type': 1,
            'currency': 1,
            'lang': 'rus',
            'shipment_point': settings.cdek_shipment_point,
            'from_location': {'code': origin['code']},
            'to_location': {'code': to_city_code},
            'packages': packages,
        }
        if delivery_point:
            payload['delivery_point'] = delivery_point
        return await self._request('POST', '/v2/calculator/tarifflist', json=payload)

    async def create_order(self, payload: dict):
        return await self._request('POST', '/v2/orders', json=payload)

cdek = CDEKClient()
