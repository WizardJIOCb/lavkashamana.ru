# Статус проекта

Проверено локально 22.09.2026:

- JavaScript проходит `node --check`.
- Backend импортируется и отвечает через FastAPI TestClient.
- Первый вход `@shamanchik007` получает admin-права.
- Начальные категории создаются seed-скриптом.
- Товар создаётся через admin API.
- Заказ создаётся и переводится в `awaiting_payment` через тестовый payment gateway.
- Автотест реферальной системы проходит: 1% ... 10%, потолок 10%.

Для реального запуска остаются внешние действия владельца:

1. VPS + домен + HTTPS.
2. `BOT_TOKEN` BotFather.
3. `CDEK_CLIENT_ID` / `CDEK_CLIENT_SECRET`.
4. Платёжные реквизиты (`YOOKASSA_*`, если используется ЮKassa).
5. `DEV_AUTH=false`.
