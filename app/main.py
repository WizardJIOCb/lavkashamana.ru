from .services import packing  # LAVKA_PACKING_V1
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import func

from .config import settings
from .db import Base, engine, get_db
from .models import User, Category, Product, Order, OrderItem, ReferralCredit, Setting, PromoCode
from .schemas import CategoryIn, ProductIn, QuoteIn, OrderIn, CartItemIn, SettingsPatch, PromoCodeIn
from .services.telegram import get_current_user, require_admin
from .services.referrals import active_referrals_count, referral_rate, credit_referrer_for_paid_order
from .services.cdek import cdek
from .services.pochta import pochta
from .services.payments import get_gateway

Base.metadata.create_all(engine)
app = FastAPI(title=settings.app_name)
ROOT = Path(__file__).resolve().parent.parent


def money(v):
    return float(Decimal(v or 0))

def user_json(db, u: User):
    active = active_referrals_count(db, u)
    rate = referral_rate(db, u)
    return {
        'id': u.id,
        'telegram_id': u.telegram_id,
        'username': u.username,
        'first_name': u.first_name,
        'full_name': u.full_name,
        'phone': u.phone,
        'email': u.email,
        'city_code': u.city_code,
        'city_name': u.city_name,
        'delivery_type': u.delivery_type,
        'delivery_point': u.delivery_point,
        'delivery_provider': u.delivery_provider,
        'postal_code': u.postal_code,
        'address': u.address,
        'is_admin': u.is_admin,
        'balance': money(u.balance),
        'active_referrals': active,
        'referral_rate': money(rate),
        'referral_link': (
            f'https://max.ru/{settings.max_bot_username}?startapp=ref_{abs(u.telegram_id)}'
            if u.telegram_id < 0
            else f'https://t.me/{settings.bot_username}?start=ref_{u.telegram_id}'
        ),
    }

def category_json(c):
    return {'id': c.id, 'name': c.name, 'slug': c.slug, 'sort_order': c.sort_order, 'active': c.active}

def product_json(p):
    return {
        **packing.product_meta(p),
        'id': p.id, 'category_id': p.category_id, 'name': p.name, 'description': p.description,
        'price': money(p.price), 'stock': p.stock, 'image_url': p.image_url,
        'weight_g': p.weight_g, 'length_cm': p.length_cm, 'width_cm': p.width_cm,
        'height_cm': p.height_cm, 'active': p.active,
    }


# LAVKA_INTRO_GATE_V1
@app.middleware("http")
async def lavka_intro_gate(request: Request, call_next):
    if (
        request.method == "GET"
        and request.url.path == "/"
        and not request.url.query
    ):
        return RedirectResponse(
            url="/launch.html?v=1790949915",
            status_code=307,
            headers={"Cache-Control":"no-store, no-cache, must-revalidate"}
        )
    return await call_next(request)

@app.put('/api/profile')
def update_profile(body: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    allowed = {
        'phone',
        'city_code',
        'city_name',
        'delivery_type',
        'delivery_point',
        'delivery_provider',
        'postal_code',
        'address',
    }

    for key in allowed:
        if key not in body:
            continue

        value = body.get(key)

        if key == 'city_code':
            setattr(user, key, int(value) if value not in (None, '') else None)
        else:
            value = str(value).strip() if value not in (None, '') else None
            setattr(user, key, value)

    db.commit()
    db.refresh(user)
    return user_json(db, user)


@app.get('/api/bootstrap')
def bootstrap(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    cats = db.query(Category).filter(Category.active.is_(True)).order_by(Category.sort_order, Category.name).all()
    products = db.query(Product).filter(Product.active.is_(True)).order_by(Product.id.desc()).all()
    orders = db.query(Order).filter(Order.user_id == user.id).order_by(Order.id.desc()).limit(20).all()
    return {
        'app_name': settings.app_name,
        'me': user_json(db, user),
        'categories': [category_json(c) for c in cats],
        'products': [product_json(p) for p in products],
        'orders': [{'id': o.id, 'status': o.status, 'total': money(o.total), 'created_at': o.created_at.isoformat()} for o in orders],
        'integrations': {
            'cdek': cdek.configured,
            'pochta': pochta.configured,
            'payment_provider': settings.payment_provider,
        },
    }

@app.get('/api/referrals')
def referrals(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    referred = db.query(User).filter(User.referrer_id == user.id).order_by(User.created_at.desc()).all()
    rows = []
    for r in referred:
        paid_count = db.query(func.count(Order.id)).filter(Order.user_id == r.id, Order.status == 'paid').scalar() or 0
        rows.append({'telegram_id': r.telegram_id, 'username': r.username, 'first_name': r.first_name, 'bought': paid_count > 0, 'paid_orders': paid_count})
    credits = db.query(ReferralCredit).filter(ReferralCredit.referrer_id == user.id).order_by(ReferralCredit.id.desc()).limit(50).all()
    return {'active_count': active_referrals_count(db, user), 'rate': money(referral_rate(db, user)), 'people': rows, 'credits': [{'order_id': c.source_order_id, 'rate': c.rate_percent, 'base': money(c.base_amount), 'amount': money(c.amount), 'created_at': c.created_at.isoformat()} for c in credits]}

def _cdek_city_query(value: str) -> str:
    """
    Позволяет искать как кириллицей:
      М, Мо, Мос

    так и латиницей:
      M, Mo, Mos
    """
    s = (value or '').strip()

    if not s:
        return ''

    if not re.search(r'[A-Za-z]', s):
        return s

    s = s.lower()

    pairs = (
        ('shch', 'щ'),
        ('sch', 'щ'),
        ('yo', 'ё'),
        ('zh', 'ж'),
        ('kh', 'х'),
        ('ts', 'ц'),
        ('ch', 'ч'),
        ('sh', 'ш'),
        ('yu', 'ю'),
        ('ya', 'я'),
        ('ye', 'е'),
    )

    for src, dst in pairs:
        s = s.replace(src, dst)

    chars = {
        'a':'а', 'b':'б', 'v':'в', 'g':'г',
        'd':'д', 'e':'е', 'z':'з', 'i':'и',
        'j':'й', 'k':'к', 'l':'л', 'm':'м',
        'n':'н', 'o':'о', 'p':'п', 'r':'р',
        's':'с', 't':'т', 'u':'у', 'f':'ф',
        'h':'х', 'c':'к', 'y':'ы', 'q':'к',
        'w':'в', 'x':'кс',
    }

    return ''.join(chars.get(ch, ch) for ch in s)


@app.get('/api/cdek/cities')
async def cdek_cities(
    q: str,
    user: User = Depends(get_current_user),
):
    q = _cdek_city_query(q)

    if not q:
        return []

    rows = await cdek.suggest_cities(q)

    result = []

    for row in rows if isinstance(rows, list) else []:
        code = row.get('code')

        full_name = str(
            row.get('full_name')
            or row.get('city')
            or row.get('name')
            or ''
        ).strip()

        if code is None or not full_name:
            continue

        parts = [
            part.strip()
            for part in full_name.split(',')
            if part.strip()
        ]

        city_name = parts[0] if parts else full_name
        region = ', '.join(parts[1:]) if len(parts) > 1 else ''

        result.append({
            'code': int(code),

            # ВАЖНО:
            # именно эти поля ожидает существующий frontend.
            'city': city_name,
            'region': region,

            'full_name': full_name,
        })

    return result[:10]


@app.get('/api/cdek/pvz')
async def cdek_pvz(city_code: int, user: User = Depends(get_current_user)):
    return await cdek.delivery_points(city_code)

@app.get('/api/pochta/postoffice/{postal_code}')
async def pochta_postoffice(
    postal_code: str,
    user: User = Depends(get_current_user),
):
    row = await pochta.postoffice_by_index(postal_code)
    return {
        'postal_code': row.get('postal-code'),
        'region': row.get('region'),
        'settlement': row.get('settlement'),
        'address': row.get('address-source'),
        'latitude': row.get('latitude'),
        'longitude': row.get('longitude'),
        'is_closed': bool(
            row.get('is-closed')
            or row.get('is-temporary-closed')
        ),
    }



@app.get('/api/pochta/postoffices')
async def pochta_postoffices(
    q: str,
    user: User = Depends(get_current_user),
):
    q = (q or '').strip()

    if len(q) < 3:
        return []

    rows = await pochta.postoffices_by_address(q, 10)

    if isinstance(rows, dict):
        rows = (
            rows.get('postoffices')
            or rows.get('offices')
            or rows.get('data')
            or ([rows] if rows.get('postal-code') else [])
        )

    result = []

    for row in rows if isinstance(rows, list) else []:
        code = str(row.get('postal-code') or '').strip()

        if not code:
            continue

        result.append({
            'postal_code': code,
            'region': row.get('region'),
            'settlement': row.get('settlement'),
            'address': row.get('address-source'),
            'latitude': row.get('latitude'),
            'longitude': row.get('longitude'),
            'is_closed': bool(
                row.get('is-closed')
                or row.get('is-temporary-closed')
            ),
        })

    return result[:10]


def cart_products(db: Session, items):
    ids = [i.product_id for i in items]
    products = {p.id: p for p in db.query(Product).filter(Product.id.in_(ids), Product.active.is_(True)).all()}
    lines = []
    for item in items:
        p = products.get(item.product_id)
        if not p:
            raise HTTPException(400, f'Product {item.product_id} not found')
        if item.qty > p.stock:
            raise HTTPException(400, f'Not enough stock: {p.name}')
        lines.append((p, item.qty))
    return lines


POCHTA_SENDER_INDEX = '452614'


async def _pochta_delivery_quote(
    lines,
    postal_code: str,
    delivery_type: str,
    db,
    packing_plan=None,
):
    code = ''.join(
        ch for ch in str(postal_code or '')
        if ch.isdigit()
    )

    if len(code) != 6:
        raise HTTPException(
            400,
            'Индекс должен содержать 6 цифр'
        )

    mode = (delivery_type or '').strip().lower()

    if mode not in {
        'pickup',
        'first_class',
        'poste_restante',
        'courier',
    }:
        raise HTTPException(
            400,
            'Неизвестный способ доставки Почты России'
        )

    office = await pochta.postoffice_by_index(code)

    if (
        office.get('is-closed')
        or office.get('is-temporary-closed')
    ):
        raise HTTPException(
            400,
            'Выбранное отделение Почты России закрыто'
        )

    packing_plan = packing_plan or packing.plan_for(db, lines)
    mass = packing_plan["package"]["weight"]
    mail_type = {
        'pickup': 'POSTAL_PARCEL',
        'first_class': 'PARCEL_CLASS_1',
        'poste_restante': 'POSTAL_PARCEL',
        'courier': 'EMS',
    }[mode]

    result = await pochta.tariff({
        'index-from': POCHTA_SENDER_INDEX,
        'index-to': code,
        'mail-category': 'ORDINARY',
        'mail-type': mail_type,
        'mass': mass,
        'dimension': {k: packing_plan["package"][k] for k in ("length", "width", "height")},
    })

    rate = result.get('total-rate')

    if rate is None or int(rate) <= 0:
        raise HTTPException(
            502,
            'Не удалось рассчитать стоимость Почты России'
        )

    price = (
        Decimal(str(rate))
        / Decimal('100')
    ).quantize(Decimal('0.01'))

    delivery_time = result.get('delivery-time') or {}

    return {
        'postal_code': code,
        'delivery_type': mode,
        'mail_type': mail_type,
        'price': price,
        'period_min': delivery_time.get('min-days'),
        'period_max': delivery_time.get('max-days'),
        'office': office,
        'packing': packing_plan,
    }


@app.post('/api/pochta/quote')
async def pochta_quote(
    body: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    raw_items = body.get('items') or []

    if not isinstance(raw_items, list) or not raw_items:
        raise HTTPException(400, 'Корзина пуста')

    try:
        items = [
            CartItemIn(
                product_id=int(x.get('product_id')),
                qty=int(x.get('qty')),
            )
            for x in raw_items
        ]
    except Exception:
        raise HTTPException(
            400,
            'Некорректные товары в корзине'
        )

    lines = cart_products(db, items)

    result = await _pochta_delivery_quote(
        lines,
        body.get('postal_code'),
        body.get('delivery_type') or 'pickup',
        db=db,
    )

    office = result['office']

    return {
        'postal_code': result['postal_code'],
        'delivery_type': result['delivery_type'],
        'mail_type': result['mail_type'],
        'price': money(result['price']),
        'packing': result['packing'],
        'period_min': result['period_min'],
        'period_max': result['period_max'],
        'office': {
            'postal_code': office.get('postal-code'),
            'region': office.get('region'),
            'settlement': office.get('settlement'),
            'address': office.get('address-source'),
        },
    }


@app.post('/api/cdek/quote')
async def cdek_quote(body: QuoteIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    lines = cart_products(db, body.items)
    plan = packing.plan_for(db, lines)
    packages = packing.cdek_packages(plan)
    result = await cdek.tariff_list(body.to_city_code, packages, body.delivery_point)
    if isinstance(result, dict):
        result["packing"] = plan
    return result

@app.post('/api/orders')
async def create_order(body: OrderIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    lines = cart_products(db, body.items)
    packing_plan = packing.plan_for(db, lines)
    email = (body.email or '').strip().lower()
    if '@' not in email or '.' not in email.rsplit('@', 1)[-1]:
        raise HTTPException(400, 'Укажите корректный email для кассового чека')
    items_total = sum((Decimal(p.price) * qty for p, qty in lines), Decimal('0'))
    promo_code = (body.promo_code or '').strip().upper()
    promo_percent = Decimal('0')
    promo_discount = Decimal('0')
    if promo_code:
        promo = db.query(PromoCode).filter(
            PromoCode.code == promo_code,
            PromoCode.active.is_(True),
        ).first()
        if not promo:
            raise HTTPException(400, 'Промокод не найден или отключён')
        if int(promo.max_uses or 0) > 0 and int(promo.used_count or 0) >= int(promo.max_uses):
            raise HTTPException(400, 'Лимит использований промокода исчерпан')
        promo_percent = Decimal(str(promo.percent or 0)).quantize(Decimal('0.01'))
        promo_discount = (items_total * promo_percent / Decimal('100')).quantize(Decimal('0.01'))
    discounted_items_total = max(Decimal('0'), items_total - promo_discount)
    requested_balance = Decimal(str(body.use_balance)).quantize(Decimal('0.01'))
    available = Decimal(user.balance or 0)
    balance_used = min(requested_balance, available, discounted_items_total)
    delivery_total = Decimal(str(body.delivery_total)).quantize(Decimal('0.01'))

    provider = (
        body.delivery_provider
        or 'cdek'
    ).strip().lower()

    if provider not in {'cdek', 'pochta'}:
        raise HTTPException(
            400,
            'Неизвестная служба доставки'
        )

    postal_code = (
        ''.join(
            ch for ch in str(body.postal_code or '')
            if ch.isdigit()
        )
        or None
    )

    order_delivery_point = body.delivery_point
    order_city_name = body.city_name
    order_address = body.address

    # Стоимость доставки всегда пересчитывает сервер.
    if provider == 'cdek':
        if not cdek.configured:
            raise HTTPException(
                503,
                'СДЭК пока не подключён'
            )

        if (
            not body.city_code
            or not body.delivery_tariff_code
        ):
            raise HTTPException(
                400,
                'Выберите город и тариф СДЭК'
            )

        packages = packing.cdek_packages(packing_plan)

        quote = await cdek.tariff_list(
            body.city_code,
            packages,
            body.delivery_point,
        )

        tariffs = (
            quote.get('tariff_codes', [])
            if isinstance(quote, dict)
            else []
        )

        expected_mode = (
            4 if body.delivery_type == 'pickup'
            else 3 if body.delivery_type == 'courier'
            else None
        )

        if expected_mode is None:
            raise HTTPException(
                400,
                'Неизвестный способ доставки СДЭК'
            )

        selected = next(
            (
                t for t in tariffs
                if int(t.get('tariff_code', -1))
                == int(body.delivery_tariff_code)
                and int(t.get('delivery_mode', 0))
                == expected_mode
            ),
            None,
        )

        if not selected:
            raise HTTPException(
                400,
                'Выбранный тариф СДЭК не соответствует способу доставки'
            )

        delivery_total = Decimal(
            str(
                selected.get(
                    'delivery_sum',
                    selected.get('total_sum', 0),
                )
            )
        ).quantize(Decimal('0.01'))

    elif provider == 'pochta':
        if not pochta.configured:
            raise HTTPException(
                503,
                'Почта России пока не подключена'
            )

        result = await _pochta_delivery_quote(
            lines,
            postal_code,
            body.delivery_type,
            db=db, packing_plan=packing_plan,
        )

        postal_code = result['postal_code']
        delivery_total = result['price']

        office = result['office']

        order_delivery_point = postal_code

        order_city_name = (
            office.get('settlement')
            or body.city_name
        )

        office_address = ', '.join(
            str(x).strip()
            for x in [
                office.get('region'),
                office.get('settlement'),
                office.get('address-source'),
            ]
            if x
        )

        if body.delivery_type in ('pickup', 'first_class'):
            order_address = (
                office_address
                or f'Индекс {postal_code}'
            )

        elif body.delivery_type == 'poste_restante':
            order_address = (
                f'До востребования, индекс {postal_code}'
                + (
                    f', {office_address}'
                    if office_address
                    else ''
                )
            )

        elif body.delivery_type == 'courier':
            address = (body.address or '').strip()

            if not address:
                raise HTTPException(
                    400,
                    'Укажите адрес доставки'
                )

            order_address = address

    total = discounted_items_total - balance_used + delivery_total
    order = Order(
        user_id=user.id, status='new', items_total=items_total, balance_used=balance_used,
        delivery_total=delivery_total, total=total, payment_provider=settings.payment_provider,
        delivery_type=body.delivery_type,
        delivery_provider=provider,
        postal_code=postal_code,
        delivery_tariff_code=(
            body.delivery_tariff_code
            if provider == 'cdek'
            else None
        ),
        delivery_point=order_delivery_point,
        city_code=(
            body.city_code
            if provider == 'cdek'
            else None
        ),
        city_name=order_city_name,
        address=order_address,
        customer_name=body.customer_name,
        phone=body.phone,
        email=email,
        promo_code=promo_code or None, promo_percent=promo_percent, promo_discount=promo_discount,
    )
    # Запоминаем данные последнего оформления для следующего заказа.
    user.full_name = body.customer_name
    user.phone = body.phone
    user.email = email
    user.city_code = (
        body.city_code
        if provider == 'cdek'
        else None
    )
    user.city_name = order_city_name
    user.delivery_type = body.delivery_type
    user.delivery_point = order_delivery_point
    user.delivery_provider = provider
    user.postal_code = postal_code
    user.address = order_address

    db.add(order); db.flush()
    packing.put_record(db, "o:" + str(order.id), packing_plan)
    for p, qty in lines:
        db.add(OrderItem(order_id=order.id, product_id=p.id, name=packing.product_order_name(p), qty=qty, unit_price=p.price, line_total=Decimal(p.price)*qty))
    if balance_used > 0:
        user.balance = available - balance_used
    db.commit(); db.refresh(order)

    if total <= 0:
        await mark_paid(db, order)
        return {'order_id': order.id, 'status': order.status, 'payment_url': None}

    gateway = get_gateway()
    receipt_db_lines = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()

    total_cents = int(Decimal(order.total or 0) * 100)
    delivery_cents = int(Decimal(order.delivery_total or 0) * 100)
    goods_cents = max(0, total_cents - delivery_cents)

    source_cents = [
        max(0, int(Decimal(x.line_total or 0) * 100))
        for x in receipt_db_lines
    ]
    source_sum = sum(source_cents)

    receipt_items = []
    remaining = goods_cents

    for idx, line in enumerate(receipt_db_lines):
        if remaining <= 0:
            break

        if idx == len(receipt_db_lines) - 1 or source_sum <= 0:
            allocated = remaining
        else:
            allocated = min(
                remaining,
                goods_cents * source_cents[idx] // source_sum
            )

        remaining -= allocated

        if allocated <= 0:
            continue

        qty = max(1, int(line.qty or 1))
        base_cents, extra = divmod(allocated, qty)
        description = (line.name or 'Товар')[:128]

        common = {
            'vat_code': 1,
            'payment_mode': 'full_prepayment',
            'payment_subject': 'commodity',
        }

        normal_qty = qty - extra

        if base_cents > 0 and normal_qty > 0:
            receipt_items.append({
                'description': description,
                'quantity': normal_qty,
                'amount': {
                    'value': f'{Decimal(base_cents) / Decimal(100):.2f}',
                    'currency': 'RUB',
                },
                **common,
            })

        if extra > 0:
            receipt_items.append({
                'description': description,
                'quantity': extra,
                'amount': {
                    'value': f'{Decimal(base_cents + 1) / Decimal(100):.2f}',
                    'currency': 'RUB',
                },
                **common,
            })

    if delivery_cents > 0:
        receipt_items.append({
            'description': (
                'Доставка Почтой России'
                if order.delivery_provider == 'pochta'
                else 'Доставка СДЭК'
            ),
            'quantity': 1,
            'amount': {
                'value': f'{Decimal(delivery_cents) / Decimal(100):.2f}',
                'currency': 'RUB',
            },
            'vat_code': 1,
            'payment_mode': 'full_prepayment',
            'payment_subject': 'service',
        })

    if not receipt_items and total_cents > 0:
        receipt_items.append({
            'description': f'Заказ №{order.id}',
            'quantity': 1,
            'amount': {
                'value': f'{Decimal(total_cents) / Decimal(100):.2f}',
                'currency': 'RUB',
            },
            'vat_code': 1,
            'payment_mode': 'full_prepayment',
            'payment_subject': 'commodity',
        })

    phone = ''.join(ch for ch in str(order.phone or '') if ch.isdigit())

    if len(phone) == 10:
        phone = '7' + phone
    elif len(phone) == 11 and phone.startswith('8'):
        phone = '7' + phone[1:]

    receipt = {
        'customer': {
            'full_name': order.customer_name or 'Покупатель',
            'phone': phone,
            'email': order.email,
        },
        'items': receipt_items,
        'internet': True,
    }

    payment = await gateway.create(
        order.id,
        Decimal(order.total),
        f'Заказ №{order.id} — {settings.app_name}',
        receipt=receipt,
    )
    order.payment_id = payment['id']
    order.payment_url = payment.get('confirmation_url')
    order.status = 'awaiting_payment'
    db.commit()
    return {'order_id': order.id, 'status': order.status, 'payment_url': order.payment_url}


async def create_cdek_shipment(db: Session, order: Order):
    if (
        order.delivery_provider != 'cdek'
        or not cdek.configured
        or order.cdek_order_uuid
    ):
        return order.cdek_order_uuid
    if not settings.cdek_shipment_point:
        raise RuntimeError('CDEK shipment point is not configured')

    plan = packing.saved_plan(db, order)
    packages = [packing.shipment_package(plan, order.id)]
    if not packages:
        raise RuntimeError('CDEK: order has no packages')

    payload = {
        'type': 1,
        'number': f'LAVKA-{order.id}',
        'tariff_code': int(order.delivery_tariff_code),
        'shipment_point': settings.cdek_shipment_point,
        'recipient': {
            'name': order.customer_name,
            'phones': [{'number': order.phone}],
        },
        'packages': packages,
        'comment': f'Лавка Шамана, заказ {order.id}',
    }

    if order.delivery_type == 'pickup':
        if not order.delivery_point:
            raise RuntimeError('CDEK: delivery point is missing')
        payload['delivery_point'] = order.delivery_point
    elif order.delivery_type == 'courier':
        if not order.city_code or not order.address:
            raise RuntimeError('CDEK: courier address is missing')
        payload['to_location'] = {
            'code': int(order.city_code),
            'address': order.address,
        }
    else:
        raise RuntimeError('CDEK: unknown delivery type')

    result = await _lavka_orderflow_submit(db, order, payload)
    requests = result.get('requests', []) if isinstance(result, dict) else []
    failed = [r for r in requests if r.get('state') == 'INVALID' or r.get('errors')]
    if failed:
        raise RuntimeError(f'CDEK rejected order: {failed}')

    entity = result.get('entity') if isinstance(result, dict) else None
    uuid = entity.get('uuid') if isinstance(entity, dict) else None
    if not uuid:
        raise RuntimeError(f'CDEK did not return order UUID: {result}')

    order.cdek_order_uuid = uuid
    db.commit()
    db.refresh(order)
    return uuid


async def create_pochta_shipment(db: Session, order: Order):
    if (
        order.delivery_provider != 'pochta'
        or not pochta.configured
    ):
        return None

    from sqlalchemy import text

    lock_connection = db.get_bind().connect()
    locked = False
    lock_params = {'g': 813009, 'i': int(order.id)}

    try:
        locked = bool(
            lock_connection.execute(
                text('SELECT pg_try_advisory_lock(:g, :i)'),
                lock_params,
            ).scalar()
        )
        if not locked:
            raise HTTPException(
                409,
                'Отправление Почты России уже обрабатывается'
            )

        record_key = 'pochta:' + str(order.id)
        saved = packing.get_record(db, record_key) or {}

        if saved.get('state') == 'submitted':
            return saved.get('response')

        if saved.get('state') in {'sending', 'uncertain'}:
            raise HTTPException(
                409,
                saved.get('error')
                or 'Нужно проверить заказ в кабинете Почты России перед повторной отправкой'
            )

        plan = packing.saved_plan(db, order)
        package = plan["package"]

        name_parts = str(order.customer_name or '').split()
        if len(name_parts) < 2:
            raise RuntimeError(
                'POCHTA: ФИО получателя заполнено не полностью'
            )

        surname = name_parts[0]
        given_name = name_parts[1]
        middle_name = ' '.join(name_parts[2:]).strip()

        phone = ''.join(
            ch for ch in str(order.phone or '')
            if ch.isdigit()
        )
        if len(phone) == 10:
            phone = '7' + phone
        elif len(phone) == 11 and phone.startswith('8'):
            phone = '7' + phone[1:]

        if len(phone) != 11:
            raise RuntimeError(
                'POCHTA: некорректный телефон получателя'
            )

        postal_code = ''.join(
            ch for ch in str(order.postal_code or '')
            if ch.isdigit()
        )
        if len(postal_code) != 6:
            raise RuntimeError(
                'POCHTA: некорректный индекс получателя'
            )

        office = await pochta.postoffice_by_index(postal_code)

        if order.delivery_type == 'courier':
            raw_address = ', '.join(
                str(x).strip()
                for x in [
                    postal_code,
                    order.city_name,
                    order.address,
                ]
                if x
            )
        elif order.delivery_type == 'poste_restante':
            raw_address = ', '.join(
                str(x).strip()
                for x in [
                    postal_code,
                    order.city_name
                    or office.get('settlement'),
                    'до востребования',
                ]
                if x
            )
        else:
            raw_address = ', '.join(
                str(x).strip()
                for x in [
                    office.get('region'),
                    office.get('settlement'),
                    office.get('address-source'),
                ]
                if x
            )

        cleaned_rows = await pochta.clean_address(raw_address)
        cleaned = (
            cleaned_rows[0]
            if isinstance(cleaned_rows, list) and cleaned_rows
            else {}
        )

        is_demand = order.delivery_type == 'poste_restante'

        place_to = (
            cleaned.get('place')
            or cleaned.get('settlement')
            or office.get('settlement')
            or order.city_name
        )

        street_to = None if is_demand else (
            cleaned.get('street')
            or office.get('address-source')
            or order.address
        )

        if not place_to or (not is_demand and not street_to):
            raise RuntimeError(
                'POCHTA: не удалось определить адрес получателя'
            )

        mail_type = {
            'pickup': 'POSTAL_PARCEL',
            'first_class': 'PARCEL_CLASS_1',
            'poste_restante': 'POSTAL_PARCEL',
            'courier': 'EMS',
        }.get(order.delivery_type)
        if not mail_type:
            raise RuntimeError('POCHTA: неизвестный способ доставки')

        item = {
            'address-type-to': (
                'DEMAND'
                if is_demand
                else 'DEFAULT'
            ),
            'mail-category': 'ORDINARY',
            'mail-direct': 643,
            'mail-type': mail_type,
            'mass': int(package['weight']),
            'dimension': {
                'length': int(package['length']),
                'width': int(package['width']),
                'height': int(package['height']),
            },
            'order-num': f'LAVKA-{order.id}',
            'recipient-name': str(order.customer_name),
            'given-name': given_name,
            'surname': surname,
            'tel-address': phone,
            'index-to': int(postal_code),
            'postoffice-code': POCHTA_SENDER_INDEX,
            'place-to': str(place_to),
            'transport-type': 'SURFACE',
        }

        if middle_name:
            item['middle-name'] = middle_name

        if street_to:
            item['street-to'] = str(street_to)

        optional_map = {
            'region': 'region-to',
            'house': 'house-to',
            'room': 'room-to',
            'corpus': 'corpus-to',
            'building': 'building-to',
            'letter': 'letter-to',
            'slash': 'slash-to',
            'area': 'area-to',
        }
        for source, target in optional_map.items():
            value = cleaned.get(source)
            if value not in (None, ''):
                item[target] = str(value)

        payload = [item]

        packing.put_record(
            db,
            record_key,
            {
                'state': 'sending',
                'payload': payload,
                'error': None,
                'attempted_at': datetime.utcnow().isoformat(),
            },
        )
        db.commit()

        try:
            result = await pochta.create_order(payload)
        except Exception as exc:
            db.rollback()
            packing.put_record(
                db,
                record_key,
                {
                    'state': 'uncertain',
                    'payload': payload,
                    'error': (
                        'Нет подтверждённого ответа Почты России. '
                        'Проверьте заказ LAVKA-'
                        + str(order.id)
                        + ' в кабинете перед повторной отправкой. '
                        + str(getattr(exc, 'detail', exc))[:500]
                    ),
                },
            )
            db.commit()
            raise

        errors = None
        if isinstance(result, dict):
            errors = result.get('errors') or result.get('error')
        elif isinstance(result, list):
            bad = [
                row for row in result
                if isinstance(row, dict)
                and (row.get('errors') or row.get('error'))
            ]
            errors = bad or None

        if errors:
            packing.put_record(
                db,
                record_key,
                {
                    'state': 'rejected',
                    'payload': payload,
                    'response': result,
                    'error': str(errors)[:1000],
                },
            )
            db.commit()
            raise RuntimeError(
                f'POCHTA rejected order: {errors}'
            )

        packing.put_record(
            db,
            record_key,
            {
                'state': 'submitted',
                'payload': payload,
                'response': result,
                'error': None,
            },
        )
        db.commit()
        return result

    finally:
        if locked:
            try:
                lock_connection.execute(
                    text('SELECT pg_advisory_unlock(:g, :i)'),
                    lock_params,
                )
            except Exception:
                pass
        lock_connection.close()


async def mark_paid(db: Session, order: Order):
    first_payment = order.status != 'paid'
    if first_payment:
        order.status = 'paid'
        order.paid_at = datetime.utcnow()
        items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
        product_ids = sorted({int(line.product_id) for line in items if line.product_id})
        locked_products = {}
        if product_ids:
            rows = (
                db.query(Product)
                .filter(Product.id.in_(product_ids))
                .with_for_update()
                .all()
            )
            locked_products = {int(p.id): p for p in rows}

        for line in items:
            if not line.product_id:
                continue
            product = locked_products.get(int(line.product_id))
            if not product:
                raise HTTPException(409, f"Товар #{line.product_id} не найден при оплате")
            if int(product.stock or 0) < int(line.qty or 0):
                raise HTTPException(
                    409,
                    f"Недостаточно остатка для товара: {product.name}"
                )
            product.stock = int(product.stock or 0) - int(line.qty or 0)
        if order.promo_code:
            promo = db.query(PromoCode).filter(PromoCode.code == order.promo_code).first()
            if promo:
                promo.used_count = int(promo.used_count or 0) + 1
                if int(promo.max_uses or 0) > 0 and promo.used_count >= int(promo.max_uses):
                    promo.active = False
        db.commit()
        db.refresh(order)
        if not order.promo_code:
            credit_referrer_for_paid_order(db, order)

    if (
        order.delivery_provider == 'cdek'
        and cdek.configured
        and not order.cdek_order_uuid
    ):
        try:
            await create_cdek_shipment(db, order)
        except Exception as exc:
            print(f'CDEK CREATE ERROR order={order.id}: {exc}')

    if (
        order.delivery_provider == 'pochta'
        and pochta.configured
        and first_payment
    ):
        try:
            await create_pochta_shipment(db, order)
        except Exception as exc:
            print(f'POCHTA CREATE ERROR order={order.id}: {exc}')

    return order


@app.get('/api/payments/mock/{payment_id}/success')
async def mock_success(payment_id: str, order_id: int, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order or order.payment_id != payment_id:
        raise HTTPException(404, 'Order not found')
    if str(order.status or '').lower().startswith('cancelled'):
        return RedirectResponse(url=f'/?cancelled={order.id}')
    await mark_paid(db, order)
    return RedirectResponse(url=f'/?paid={order.id}')

@app.post('/api/payments/yookassa/webhook')
async def yookassa_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    obj = payload.get('object') or {}
    payment_id = obj.get('id')
    if not payment_id:
        return {'ok': True}
    order = db.query(Order).filter(Order.payment_id == payment_id).first()
    if not order:
        return {'ok': True}
    gateway = get_gateway()
    payment = await gateway.fetch(payment_id)
    if payment.get('status') == 'succeeded' and payment.get('paid', True):
        if str(order.status or '').lower().startswith('cancelled'):
            refunded = Decimal(str((payment.get('refunded_amount') or {}).get('value') or '0'))
            amount = Decimal(order.total or 0)
            if amount > 0 and refunded < amount:
                await gateway.refund(
                    payment_id,
                    amount,
                    idempotence_key=f'lavka-cancelled-order-{order.id}',
                )
            return {'ok': True}
        await mark_paid(db, order)
    return {'ok': True}

# ---------------- Admin ----------------
@app.get('/api/admin/dashboard')
def admin_dashboard(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    users = db.query(func.count(User.id)).scalar() or 0
    paid_orders = db.query(func.count(Order.id)).filter(Order.status == 'paid').scalar() or 0
    revenue = db.query(func.coalesce(func.sum(Order.total), 0)).filter(Order.status == 'paid').scalar() or 0
    return {'users': users, 'paid_orders': paid_orders, 'revenue': money(revenue)}

@app.get('/api/admin/categories')
def admin_categories(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [category_json(c) for c in db.query(Category).order_by(Category.sort_order, Category.id).all()]

@app.post('/api/admin/categories')
def admin_category_create(body: CategoryIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    c = Category(**body.model_dump()); db.add(c); db.commit(); db.refresh(c); return category_json(c)

@app.put('/api/admin/categories/{category_id}')
def admin_category_update(category_id: int, body: CategoryIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    c = db.get(Category, category_id)
    if not c: raise HTTPException(404, 'Category not found')
    for k,v in body.model_dump().items(): setattr(c,k,v)
    db.commit(); return category_json(c)

@app.delete('/api/admin/categories/{category_id}')
def admin_category_delete(category_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    c = db.get(Category, category_id)
    if not c: raise HTTPException(404, 'Category not found')
    db.delete(c); db.commit(); return {'ok': True}

@app.post('/api/admin/upload')
async def admin_upload(file: UploadFile = File(...), admin: User = Depends(require_admin)):
    allowed = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp'}
    if file.content_type not in allowed:
        raise HTTPException(400, 'Разрешены JPG, PNG и WEBP')
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(400, 'Файл больше 15 МБ')
    import uuid
    upload_dir = ROOT / 'static' / 'uploads'
    upload_dir.mkdir(exist_ok=True)
    name = f'{uuid.uuid4().hex}{allowed[file.content_type]}'
    (upload_dir / name).write_bytes(data)
    return {'url': f'/uploads/{name}'}

@app.get('/api/admin/products')
def admin_products(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [product_json(p) for p in db.query(Product).order_by(Product.id.desc()).all()]

@app.post('/api/admin/products')
def admin_product_create(body: ProductIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    p = Product(**body.model_dump(exclude={"shipping_ready", "allow_rotation"})); db.add(p); packing.save_product_meta(db, p, body); db.commit(); db.refresh(p); return product_json(p)

@app.put('/api/admin/products/{product_id}')
def admin_product_update(product_id: int, body: ProductIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    p = db.get(Product, product_id)
    if not p: raise HTTPException(404, 'Product not found')
    for k,v in body.model_dump(exclude={"shipping_ready", "allow_rotation"}).items(): setattr(p,k,v)
    packing.save_product_meta(db, p, body); db.commit(); return product_json(p)

@app.delete('/api/admin/products/{product_id}')
def admin_product_delete(product_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    p = db.get(Product, product_id)
    if not p: raise HTTPException(404, 'Product not found')
    db.delete(p); db.commit(); return {'ok': True}

@app.get('/api/admin/orders')
def admin_orders(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(Order).order_by(Order.id.desc()).limit(200).all()
    return [{'id':o.id,'status':o.status,'total':money(o.total),'customer_name':o.customer_name,'phone':o.phone,'delivery_type':o.delivery_type,'city_name':o.city_name,'address':o.address,'created_at':o.created_at.isoformat()} for o in rows]

@app.patch('/api/admin/orders/{order_id}/status')
async def admin_order_status(order_id: int, status: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, 'Order not found')

    current = str(o.status or '').lower()
    target = str(status or '').strip().lower()

    if target not in {'paid', 'cancelled'}:
        raise HTTPException(400, 'Допустимые статусы: paid, cancelled')

    if current == 'paid':
        if target == 'paid':
            return {'ok': True, 'status': 'paid'}
        raise HTTPException(400, 'Оплаченный заказ нельзя отменить')

    if target == 'paid':
        if current.startswith('cancelled'):
            raise HTTPException(400, 'Отменённый заказ нельзя перевести в оплату')
        await mark_paid(db, o)
        return {'ok': True, 'status': 'paid'}

    if current == 'cancelled_refunded':
        return {'ok': True, 'status': 'cancelled'}

    if str(o.payment_provider or '').lower() == 'yookassa' and o.payment_id:
        gateway = get_gateway()
        payment = await gateway.fetch(o.payment_id)
        payment_status = str(payment.get('status') or '').lower()
        if payment_status == 'succeeded':
            await mark_paid(db, o)
            raise HTTPException(409, 'Платёж уже прошёл в ЮKassa. Заказ отмечен оплаченным.')
        if payment_status == 'waiting_for_capture':
            await gateway.cancel(o.payment_id)

    user = db.get(User, o.user_id)
    refund_balance = Decimal(o.balance_used or 0)
    if refund_balance > 0 and user:
        user.balance = Decimal(user.balance or 0) + refund_balance

    o.status = 'cancelled_refunded'
    db.commit()
    return {'ok': True, 'status': 'cancelled', 'balance_refunded': money(refund_balance)}


@app.delete('/api/admin/orders/{order_id}')
async def admin_order_delete(order_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, 'Order not found')

    current = str(o.status or '').lower()
    if current == 'paid':
        raise HTTPException(400, 'Оплаченный заказ удалять нельзя')

    if db.query(ReferralCredit).filter(ReferralCredit.source_order_id == o.id).first():
        raise HTTPException(400, 'У заказа есть реферальное начисление. Удаление запрещено.')

    if str(o.payment_provider or '').lower() == 'yookassa' and o.payment_id:
        gateway = get_gateway()
        payment = await gateway.fetch(o.payment_id)
        payment_status = str(payment.get('status') or '').lower()
        if payment_status == 'succeeded':
            await mark_paid(db, o)
            raise HTTPException(409, 'Платёж уже прошёл в ЮKassa. Заказ отмечен оплаченным.')
        if payment_status not in {'canceled', 'cancelled'}:
            raise HTTPException(
                409,
                'Платёж в ЮKassa ещё активен. Сначала отмените заказ; удалить его можно после окончательной отмены платежа.'
            )

    if current != 'cancelled_refunded':
        user = db.get(User, o.user_id)
        refund_balance = Decimal(o.balance_used or 0)
        if refund_balance > 0 and user:
            user.balance = Decimal(user.balance or 0) + refund_balance

    db.query(OrderItem).filter(OrderItem.order_id == o.id).delete(synchronize_session=False)
    db.delete(o)
    db.commit()
    return {'ok': True}

def promo_json(p: PromoCode):
    limit = int(p.max_uses or 0)
    used = int(p.used_count or 0)
    return {
        'id': p.id,
        'code': p.code,
        'percent': money(p.percent),
        'max_uses': limit,
        'used_count': used,
        'remaining': None if limit == 0 else max(0, limit - used),
        'active': bool(p.active),
    }

@app.get('/api/admin/promocodes')
def admin_promocodes(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [promo_json(p) for p in db.query(PromoCode).order_by(PromoCode.id.desc()).all()]

@app.post('/api/admin/promocodes')
def admin_promocode_create(body: PromoCodeIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    code = (body.code or '').strip().upper()
    if not code:
        raise HTTPException(400, 'Укажи промокод')
    if db.query(PromoCode).filter(PromoCode.code == code).first():
        raise HTTPException(400, 'Такой промокод уже существует')
    p = PromoCode(
        code=code,
        percent=Decimal(str(body.percent)).quantize(Decimal('0.01')),
        max_uses=int(body.max_uses),
        active=bool(body.active),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return promo_json(p)

@app.put('/api/admin/promocodes/{promo_id}')
def admin_promocode_update(promo_id: int, body: PromoCodeIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    p = db.get(PromoCode, promo_id)
    if not p:
        raise HTTPException(404, 'Промокод не найден')
    code = (body.code or '').strip().upper()
    if not code:
        raise HTTPException(400, 'Укажи промокод')
    duplicate = db.query(PromoCode).filter(PromoCode.code == code, PromoCode.id != promo_id).first()
    if duplicate:
        raise HTTPException(400, 'Такой промокод уже существует')
    p.code = code
    p.percent = Decimal(str(body.percent)).quantize(Decimal('0.01'))
    p.max_uses = int(body.max_uses)
    p.active = bool(body.active)
    db.commit()
    db.refresh(p)
    return promo_json(p)

@app.delete('/api/admin/promocodes/{promo_id}')
def admin_promocode_delete(promo_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    p = db.get(PromoCode, promo_id)
    if not p:
        raise HTTPException(404, 'Промокод не найден')
    db.delete(p)
    db.commit()
    return {'ok': True}

@app.get('/api/admin/settings')
def admin_settings(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return {s.key:s.value for s in db.query(Setting).all()}

@app.patch('/api/admin/settings')
def admin_settings_patch(body: SettingsPatch, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    for k,v in body.values.items():
        s = db.get(Setting, k)
        if not s: s = Setting(key=k, value=v); db.add(s)
        else: s.value = v
    db.commit(); return {'ok': True}


# LAVKA PUBLIC INFO API
@app.get('/api/info')
def public_info(db: Session = Depends(get_db)):
    allowed = {
        'info_about',
        'info_delivery',
        'info_payment',
        'info_returns',
        'info_support',
        'info_documents',
        'info_privacy',
        'info_consent',
        'info_offer',
        'info_seller',
        'faq_data',
    }
    rows = db.query(Setting).filter(Setting.key.in_(allowed)).all()
    return {row.key: row.value for row in rows}


# LAVKA_ADMIN_SALES_V1_START

@app.get('/api/admin/sales-overview')
async def admin_sales_overview(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not user.is_admin:
        raise HTTPException(status_code=403, detail='Нет доступа')

    orders = (
        db.query(Order)
        .order_by(Order.id.desc())
        .all()
    )

    paid_orders = [
        o for o in orders
        if str(o.status or '').lower() == 'paid'
    ]

    customer_ids = {
        o.user_id
        for o in paid_orders
        if o.user_id is not None
    }

    # Продажи магазина:
    # только реально оплаченные товары,
    # без стоимости доставки СДЭК.
    sales_total = sum(
        (
            Decimal(o.items_total or 0) -
            Decimal(o.promo_discount or 0) -
            Decimal(o.balance_used or 0)
            for o in paid_orders
        ),
        Decimal('0')
    )

    rows = []

    for o in orders:
        rows.append({
            'id': o.id,
            'user_id': o.user_id,
            'status': o.status,
            'customer_name': o.customer_name,
            'phone': o.phone,
            'email': getattr(o, 'email', None),
            'city_name': o.city_name,
            'address': o.address,
            'delivery_type': o.delivery_type,
            'items_total': money(o.items_total),
            'promo_code': getattr(o, 'promo_code', None),
            'promo_percent': money(getattr(o, 'promo_percent', 0)),
            'promo_discount': money(getattr(o, 'promo_discount', 0)),
            'balance_used': money(o.balance_used),
            'delivery_total': money(o.delivery_total),
            'total': money(o.total),
        })

    return {
        'stats': {
            'all_orders': len(orders),
            'paid_orders': len(paid_orders),
            'customers': len(customer_ids),
            'sales_total': money(sales_total),
        },
        'orders': rows,
    }

# LAVKA_ADMIN_SALES_V1_END


# LAVKA_PRODUCT_DOCUMENTS_V1_START

@app.get('/api/product-documents')
def product_documents():
    folder = ROOT / 'static' / 'document'
    folder.mkdir(parents=True, exist_ok=True)

    allowed = {'.jpg', '.jpeg', '.png', '.webp', '.pdf'}

    files = sorted(
        [
            p for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in allowed
        ],
        key=lambda p: p.name.lower()
    )

    return [
        {
            'title': f'Документ {i}',
            'filename': p.name,
            'type': 'pdf' if p.suffix.lower() == '.pdf' else 'image',
        }
        for i, p in enumerate(files, 1)
    ]

# LAVKA_PRODUCT_DOCUMENTS_V1_END

@app.get('/api/admin/packing')
def admin_packing_get(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return packing.config_for(db)


@app.put('/api/admin/packing')
def admin_packing_update(body: dict, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    value = packing.validate_config(body)
    packing.put_record(db, 'config', value)
    db.commit()
    return value


@app.get('/api/admin/orders/{order_id}/packing')
def admin_order_packing(order_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, 'Заказ не найден')
    return packing.saved_plan(db, order)


app.mount('/', StaticFiles(directory=ROOT / 'static', html=True), name='static')


# LAVKA_PACKING_V1: admin-only packaging settings and saved assembly plans.



# LAVKA_ORDERFLOW_V1
from .services.orderflow import install as _lavka_install_orderflow
_lavka_install_orderflow(globals())
