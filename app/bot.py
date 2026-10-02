import asyncio
import io
import mimetypes
import ssl
from collections import defaultdict

import httpx
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)

from .config import settings

MAX_API = "https://platform-api2.max.ru"
SSL_CONTEXT = ssl.create_default_context(cafile="/etc/ssl/certs/ca-certificates.crt")
_media_groups: dict[str, list[Message]] = defaultdict(list)
_media_group_tasks: dict[str, asyncio.Task] = {}


def _text(message: Message) -> str:
    return message.text or message.caption or ""


def _split_text(text: str, limit: int = 4000) -> list[str]:
    if not text:
        return []
    parts = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit + 1)
        if cut < limit // 2:
            cut = limit
        parts.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    if rest:
        parts.append(rest)
    return parts


async def _download_telegram_media(bot: Bot, message: Message):
    media_type = None
    file_id = None
    filename = None
    mime_type = None

    if message.photo:
        media_type = "image"
        file_id = message.photo[-1].file_id
        filename = f"photo_{message.message_id}.jpg"
        mime_type = "image/jpeg"
    elif message.video:
        media_type = "video"
        file_id = message.video.file_id
        filename = message.video.file_name or f"video_{message.message_id}.mp4"
        mime_type = message.video.mime_type or "video/mp4"
    elif message.animation:
        media_type = "video"
        file_id = message.animation.file_id
        filename = message.animation.file_name or f"animation_{message.message_id}.mp4"
        mime_type = message.animation.mime_type or "video/mp4"
    elif message.video_note:
        media_type = "video"
        file_id = message.video_note.file_id
        filename = f"video_note_{message.message_id}.mp4"
        mime_type = "video/mp4"
    elif message.audio:
        media_type = "audio"
        file_id = message.audio.file_id
        filename = message.audio.file_name or f"audio_{message.message_id}.mp3"
        mime_type = message.audio.mime_type or "audio/mpeg"
    elif message.voice:
        media_type = "audio"
        file_id = message.voice.file_id
        filename = f"voice_{message.message_id}.ogg"
        mime_type = message.voice.mime_type or "audio/ogg"
    elif message.document:
        media_type = "file"
        file_id = message.document.file_id
        filename = message.document.file_name or f"file_{message.message_id}"
        mime_type = message.document.mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"

    if not file_id:
        return None

    buf = io.BytesIO()
    await bot.download(file_id, destination=buf)
    return media_type, filename, mime_type, buf.getvalue()


async def _find_max_token(obj):
    if isinstance(obj, dict):
        token = obj.get("token")
        if isinstance(token, str) and token:
            return token
        for value in obj.values():
            found = await _find_max_token(value)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = await _find_max_token(value)
            if found:
                return found
    return None


async def _upload_to_max(client, media_type, filename, mime_type, data):
    headers = {"Authorization": settings.max_bot_token}

    r = await client.post(
        f"{MAX_API}/uploads",
        params={"type": media_type},
        headers=headers,
    )
    r.raise_for_status()
    info = r.json()

    upload_url = info.get("url")
    if not upload_url:
        raise RuntimeError(f"MAX did not return upload URL for {filename}: {info!r}")

    token = await _find_max_token(info)

    uploaded = await client.post(
        upload_url,
        headers=headers,
        files={"data": (filename, data, mime_type)},
    )
    uploaded.raise_for_status()

    if not token:
        try:
            token = await _find_max_token(uploaded.json())
        except Exception:
            token = None

    if not token:
        body = uploaded.text[:500]
        raise RuntimeError(
            f"MAX did not return upload token for {filename}; response={body!r}"
        )

    return {"type": media_type, "payload": {"token": token}}


def _max_inline_keyboard() -> dict:
    return {
        "type": "inline_keyboard",
        "payload": {
            "buttons": [
                [
                    {
                        "type": "link",
                        "text": "💬 Консультация",
                        "url": "https://max.ru/u/f9LHodD0cOLGv-YpKtYP9VYVxRrjpclfcl0BYdv3jPEzpRJcwxjNbmDAjqw",
                    }
                ],
                [
                    {
                        "type": "link",
                        "text": "⭐ Отзывы в ТГ",
                        "url": "https://t.me/+yRqvyOdm_c4yODEy",
                    }
                ],
                [
                    {
                        "type": "link",
                        "text": "🛒 Лавка в ТГ",
                        "url": "https://t.me/Lavkashamanbot?startapp",
                    }
                ],
                [
                    {
                        "type": "link",
                        "text": "🌐 Сайт Лавки Шамана",
                        "url": "https://shamanchik.ru",
                    }
                ],
            ]
        },
    }


async def _send_max(client: httpx.AsyncClient, text: str = "", attachments: list[dict] | None = None):
    chunks = _split_text(text)
    first_text = chunks[0] if chunks else ""

    if not first_text and not attachments:
        return

    body = {}
    if first_text:
        body["text"] = first_text

    body["attachments"] = list(attachments or [])
    body["attachments"].append(_max_inline_keyboard())

    headers = {"Authorization": settings.max_bot_token}
    params = {"chat_id": settings.max_channel_id}

    delays = (0, 1, 2, 4, 7)
    last_response = None
    for delay in delays:
        if delay:
            await asyncio.sleep(delay)
        r = await client.post(f"{MAX_API}/messages", params=params, headers=headers, json=body)
        last_response = r
        if r.is_success:
            break
        if "attachment.not.ready" not in r.text and "not.processed" not in r.text:
            r.raise_for_status()
    else:
        last_response.raise_for_status()

    for extra in chunks[1:]:
        await asyncio.sleep(0.6)
        r = await client.post(
            f"{MAX_API}/messages",
            params=params,
            headers=headers,
            json={"text": extra},
        )
        r.raise_for_status()


async def _forward_single(bot: Bot, message: Message):
    async with httpx.AsyncClient(verify=SSL_CONTEXT, timeout=120.0, follow_redirects=True) as client:
        attachment = None
        media = await _download_telegram_media(bot, message)
        if media:
            attachment = await _upload_to_max(client, *media)
        await _send_max(client, _text(message), [attachment] if attachment else None)


async def _forward_media_group(bot: Bot, messages: list[Message]):
    messages.sort(key=lambda m: m.message_id)
    text = next((_text(m) for m in messages if _text(m)), "")

    # MAX supports combined image/video posts. Files/audio are sent separately.
    visual = []
    other = []
    for message in messages:
        if message.photo or message.video or message.animation or message.video_note:
            visual.append(message)
        else:
            other.append(message)

    async with httpx.AsyncClient(verify=SSL_CONTEXT, timeout=180.0, follow_redirects=True) as client:
        if visual:
            attachments = []
            for message in visual[:11]:
                media = await _download_telegram_media(bot, message)
                if media:
                    attachments.append(await _upload_to_max(client, *media))
            await _send_max(client, text, attachments)
            text = ""

        for message in other:
            media = await _download_telegram_media(bot, message)
            attachment = await _upload_to_max(client, *media) if media else None
            await _send_max(client, text or _text(message), [attachment] if attachment else None)
            text = ""
            await asyncio.sleep(0.6)


# COMMENTS_THREAD_V2
DISCUSSION_CHAT_ID = -1003995600729
DISCUSSION_USERNAME = "shamankoment"
_discussion_links: dict[int, str] = {}
_button_targets: set[int] = set()
_button_locks: dict[int, asyncio.Lock] = {}


async def _attach_channel_buttons(bot: Bot, message_id: int, comment_url: str | None = None):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    lock = _button_locks.setdefault(message_id, asyncio.Lock())
    async with lock:
        if comment_url is None:
            _button_targets.add(message_id)
            for _ in range(6):
                comment_url = _discussion_links.get(message_id)
                if comment_url:
                    break
                await asyncio.sleep(1)
        else:
            _discussion_links[message_id] = comment_url

        rows = []
        if comment_url:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="💬 Оставить комментарий",
                        url=comment_url,
                    )
                ]
            )

        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text="🟣 Лавка MAX",
                        url="https://max.ru/id26509411367_biz",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="💬 Консультация",
                        url="https://t.me/Shamanchik007",
                    ),
                    InlineKeyboardButton(
                        text="⭐ Отзывы",
                        url="https://t.me/+yRqvyOdm_c4yODEy",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="🌐 Сайт Лавки Шамана",
                        url="https://shamanchik.ru",
                    )
                ],
            ]
        )

        kb = InlineKeyboardMarkup(inline_keyboard=rows)
        last_exc = None
        for attempt in range(5):
            try:
                await bot.edit_message_reply_markup(
                    chat_id=settings.telegram_source_channel_id,
                    message_id=message_id,
                    reply_markup=kb,
                )
                print(
                    f"Telegram buttons attached message={message_id} "
                    f"comments={'yes' if comment_url else 'no'} attempt={attempt + 1}",
                    flush=True,
                )
                if comment_url:
                    _button_targets.discard(message_id)
                return
            except Exception as exc:
                last_exc = exc
                if attempt == 4:
                    raise
                await asyncio.sleep(2 * (attempt + 1))


def _is_discussion_forward(message) -> bool:
    return (
        message.chat.id == DISCUSSION_CHAT_ID
        and bool(getattr(message, "is_automatic_forward", False))
    )


async def _capture_discussion_forward(message, bot: Bot):
    origin = getattr(message, "forward_origin", None)
    source_message_id = getattr(origin, "message_id", None)
    source_chat = getattr(origin, "chat", None)

    if source_message_id is None:
        source_message_id = getattr(message, "forward_from_message_id", None)
    if source_chat is None:
        source_chat = getattr(message, "forward_from_chat", None)

    if not source_message_id:
        return

    if source_chat is not None:
        source_chat_id = getattr(source_chat, "id", None)
        if source_chat_id != settings.telegram_source_channel_id:
            return

    comment_url = (
        f"https://t.me/{DISCUSSION_USERNAME}/{message.message_id}"
        f"?thread={message.message_id}"
    )

    _discussion_links[source_message_id] = comment_url

    print(
        f"Discussion thread linked source={source_message_id} "
        f"group_message={message.message_id}",
        flush=True,
    )

    if source_message_id not in _button_targets:
        return

    try:
        await _attach_channel_buttons(
            bot,
            source_message_id,
            comment_url=comment_url,
        )
    except Exception as exc:
        print(
            f"Telegram comment button error source={source_message_id}: {exc!r}",
            flush=True,
        )




# COMMENT_BUTTON_V2
async def _comment_button_channel_post(message, bot):
    username = (getattr(message.chat, "username", None) or "").lower()
    if username and username != "shamangrib":
        return

    comment_url = f"https://t.me/ShamanGrib/{message.message_id}?comment=1"

    helper = globals().get("_attach_channel_buttons")
    if helper:
        try:
            await helper(
                bot,
                message.message_id,
                comment_url=comment_url,
            )
            return
        except TypeError:
            pass

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 Оставить комментарий",
                    url=comment_url,
                )
            ]
        ]
    )

    await bot.edit_message_reply_markup(
        chat_id=message.chat.id,
        message_id=message.message_id,
        reply_markup=keyboard,
    )

async def main():
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not configured")

    bot = Bot(settings.bot_token)
    dp = Dispatcher()
    dp.channel_post.register(_comment_button_channel_post)
    dp.message.register(_capture_discussion_forward, _is_discussion_forward)

    @dp.message(CommandStart())
    async def start(message: Message):
        text = message.text or ""
        payload = text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else ""
        url = settings.app_url.rstrip("/") + "/?v=introfinal1790949382"
        if payload.startswith("ref_") and payload[4:].isdigit():
            url += f"?ref={payload[4:]}"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Открыть Лавку шамана",
                        web_app=WebAppInfo(url=url),
                    )
                ]
            ]
        )
        await message.answer(
            "Добро пожаловать в «Лавку шамана». Каталог, заказы и личный кабинет открываются внутри Telegram.",
            reply_markup=kb,
        )

    @dp.channel_post()
    async def bridge_to_max(message: Message):
        if message.chat.id != settings.telegram_source_channel_id:
            return
        if not message.media_group_id:
            try:
                await _attach_channel_buttons(bot, message.message_id)
            except Exception as exc:
                print(f"Telegram buttons error for message {message.message_id}: {exc!r}", flush=True)

        if not settings.max_bot_token or not settings.max_channel_id:
            return

        try:
            if message.media_group_id:
                group_id = str(message.media_group_id)
                _media_groups[group_id].append(message)
                if group_id not in _media_group_tasks:
                    async def flush():
                        await asyncio.sleep(1.5)
                        messages = _media_groups.pop(group_id, [])
                        _media_group_tasks.pop(group_id, None)
                        if messages:
                            messages.sort(key=lambda m: m.message_id)
                            try:
                                await _attach_channel_buttons(bot, messages[-1].message_id)
                            except Exception as exc:
                                print(f"Telegram album buttons error {group_id}: {exc!r}", flush=True)
                            try:
                                await _forward_media_group(bot, messages)
                            except Exception as exc:
                                print(f"Telegram -> MAX album error {group_id}: {exc!r}", flush=True)
                    _media_group_tasks[group_id] = asyncio.create_task(flush())
                return

            await _forward_single(bot, message)
        except Exception as exc:
            print(f"Telegram -> MAX error for message {message.message_id}: {exc!r}", flush=True)

    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="Лавка шамана",
            web_app=WebAppInfo(url=settings.app_url.rstrip("/") + "/?v=introfinal1790949382"),
        )
    )
    await dp.start_polling(bot, allowed_updates=["message","channel_post","edited_channel_post"])


if __name__ == "__main__":
    asyncio.run(main())
