from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy.orm import Session
from sqlalchemy import func
from ..models import User, Order, ReferralCredit


def active_referrals_count(db: Session, referrer: User) -> int:
    return (
        db.query(func.count(func.distinct(User.id)))
        .join(Order, Order.user_id == User.id)
        .filter(User.referrer_id == referrer.id, Order.status == 'paid')
        .scalar()
        or 0
    )


def referral_rate(db: Session, referrer: User) -> Decimal:
    # 0.5 процентного пункта за каждого купившего приглашённого.
    # Максимальная ставка — 10%.
    active = active_referrals_count(db, referrer)
    return min(
        Decimal(active) * Decimal('0.5'),
        Decimal('10.0'),
    )


def credit_referrer_for_paid_order(db: Session, order: Order) -> ReferralCredit | None:
    buyer = db.get(User, order.user_id)
    if not buyer or not buyer.referrer_id:
        return None
    existing = db.query(ReferralCredit).filter(ReferralCredit.source_order_id == order.id).first()
    if existing:
        return existing
    referrer = db.get(User, buyer.referrer_id)
    if not referrer:
        return None

    # The buyer's first paid order activates them, so it participates in the new rate immediately.
    rate = referral_rate(db, referrer)
    if rate <= 0:
        return None
    # Реферальная база:
    # только фактически оплаченная стоимость товаров.
    # Стоимость доставки СДЭК полностью исключена.
    # Использованный покупателем внутренний баланс также вычитается.
    base = Decimal(order.items_total or 0) - Decimal(order.balance_used or 0)

    if base < 0:
        base = Decimal('0')

    amount = (
        base * rate / Decimal('100')
    ).quantize(
        Decimal('0.01'),
        rounding=ROUND_HALF_UP,
    )
    credit = ReferralCredit(
        referrer_id=referrer.id,
        referred_user_id=buyer.id,
        source_order_id=order.id,
        rate_percent=rate,
        base_amount=base,
        amount=amount,
    )
    referrer.balance = Decimal(referrer.balance or 0) + amount
    db.add(credit)
    db.commit()
    db.refresh(credit)
    return credit
