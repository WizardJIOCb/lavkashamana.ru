import asyncio
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, MenuButtonWebApp
from .config import settings

async def main():
    if not settings.bot_token:
        raise RuntimeError('BOT_TOKEN is not configured')
    bot = Bot(settings.bot_token)
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def start(message: Message):
        text = message.text or ''
        payload = text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else ''
        url = settings.app_url.rstrip('/') + '/'
        if payload.startswith('ref_') and payload[4:].isdigit():
            url += f'?ref={payload[4:]}'
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Открыть Лавку шамана', web_app=WebAppInfo(url=url))]])
        await message.answer('Добро пожаловать в «Лавку шамана». Каталог, заказы и личный кабинет открываются внутри Telegram.', reply_markup=kb)

    await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text='Лавка шамана', web_app=WebAppInfo(url=settings.app_url.rstrip('/') + '/')))
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
