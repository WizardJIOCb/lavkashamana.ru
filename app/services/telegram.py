import hashlib, hmac, json, time
from urllib.parse import parse_qsl
from fastapi import Header, HTTPException, Depends
from sqlalchemy.orm import Session
from ..config import settings
from ..db import get_db
from ..models import User


def validate_init_data(init_data: str) -> dict:
    if not settings.bot_token:
        raise HTTPException(503, 'BOT_TOKEN is not configured')
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop('hash', None)
    if not received_hash:
        raise HTTPException(401, 'Telegram hash missing')
    data_check_string = '\n'.join(f'{k}={v}' for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b'WebAppData', settings.bot_token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        raise HTTPException(401, 'Invalid Telegram signature')
    auth_date = int(pairs.get('auth_date', '0') or 0)
    if auth_date and time.time() - auth_date > 86400:
        raise HTTPException(401, 'Expired Telegram session')
    user = json.loads(pairs.get('user', '{}'))
    pairs['user_obj'] = user
    return pairs


def ensure_user(db: Session, tg: dict, ref_telegram_id: int | None = None) -> User:
    telegram_id = int(tg['id'])
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if not user:
        user = User(
            telegram_id=telegram_id,
            username=tg.get('username'),
            first_name=tg.get('first_name'),
        )
        db.add(user)
        db.flush()
    else:
        user.username = tg.get('username') or user.username
        user.first_name = tg.get('first_name') or user.first_name

    # One-time bootstrap: the configured username becomes admin on first verified login.
    if not db.query(User).filter(User.is_admin.is_(True)).first():
        if (user.username or '').lower() == settings.admin_telegram_username.lower().lstrip('@'):
            user.is_admin = True

    if ref_telegram_id and not user.referrer_id and ref_telegram_id != telegram_id:
        referrer = db.query(User).filter(User.telegram_id == ref_telegram_id).first()
        if referrer:
            user.referrer_id = referrer.id
    db.commit()
    db.refresh(user)
    return user


def get_current_user(
    x_telegram_init_data: str | None = Header(None),
    x_dev_telegram_id: str | None = Header(None),
    x_dev_username: str | None = Header(None),
    x_referrer_telegram_id: str | None = Header(None),
    db: Session = Depends(get_db),
):
    ref_id = int(x_referrer_telegram_id) if x_referrer_telegram_id and x_referrer_telegram_id.isdigit() else None
    if x_telegram_init_data:
        parsed = validate_init_data(x_telegram_init_data)
        tg = parsed.get('user_obj') or {}
        if not tg.get('id'):
            raise HTTPException(401, 'Telegram user missing')
        return ensure_user(db, tg, ref_id)
    if settings.dev_auth:
        tg = {
            'id': int(x_dev_telegram_id or 777000),
            'username': x_dev_username or settings.admin_telegram_username,
            'first_name': 'Dev',
        }
        return ensure_user(db, tg, ref_id)
    raise HTTPException(401, 'Telegram authorization required')


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, 'Admin only')
    return user
