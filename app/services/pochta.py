import httpx
from fastapi import HTTPException

from ..config import settings


class PochtaClient:
    @property
    def configured(self) -> bool:
        return bool(
            settings.pochta_auth_token
            and settings.pochta_user_auth_key
        )

    def _headers(self) -> dict[str, str]:
        if not self.configured:
            raise HTTPException(
                503,
                'Почта России: данные доступа не настроены'
            )

        return {
            'Authorization': f'AccessToken {settings.pochta_auth_token}',
            'X-User-Authorization': f'Basic {settings.pochta_user_auth_key}',
            'Content-Type': 'application/json;charset=UTF-8',
            'Accept': 'application/json;charset=UTF-8',
        }

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs,
    ):
        url = f'{settings.pochta_api_base.rstrip("/")}/{path.lstrip("/")}'

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.request(
                method,
                url,
                headers=self._headers(),
                **kwargs,
            )

        if response.status_code >= 400:
            raise HTTPException(
                response.status_code,
                f'Почта России: {response.text[:500]}'
            )

        if not response.content:
            return {}

        return response.json()

    async def clean_address(self, address: str):
        payload = [
            {
                'id': '1',
                'original-address': address,
            }
        ]

        return await self._request(
            'POST',
            '/1.0/clean/address',
            json=payload,
        )

    async def tariff(self, payload: dict):
        return await self._request(
            'POST',
            '/1.0/tariff',
            json=payload,
        )

    async def create_order(self, payload: list[dict]):
        return await self._request(
            'PUT',
            '/1.0/user/backlog',
            json=payload,
        )

    async def postoffice_by_index(self, postal_code: str):
        code = ''.join(ch for ch in str(postal_code) if ch.isdigit())
        if len(code) != 6:
            raise HTTPException(400, 'Индекс должен содержать 6 цифр')
        return await self._request(
            'GET',
            f'/postoffice/1.0/{code}',
        )

    async def postoffices_by_address(self, address: str, top: int = 10):
        address = (address or '').strip()
        if not address:
            raise HTTPException(400, 'Укажите адрес')
        return await self._request(
            'GET',
            '/postoffice/1.0/by-address',
            params={
                'address': address,
                'top': max(1, min(int(top), 20)),
            },
        )

    async def settlement_office_codes(
        self,
        settlement: str,
        region: str | None = None,
        district: str | None = None,
    ):
        params = {
            'settlement': (settlement or '').strip(),
        }
        if region:
            params['region'] = region.strip()
        if district:
            params['district'] = district.strip()

        if not params['settlement']:
            raise HTTPException(400, 'Укажите населённый пункт')

        return await self._request(
            'GET',
            '/postoffice/1.0/settlement.offices.codes',
            params=params,
        )


pochta = PochtaClient()
