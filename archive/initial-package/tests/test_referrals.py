from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db import Base
from app.models import User, Order
from app.services.referrals import referral_rate, credit_referrer_for_paid_order

def test_referral_rate_and_credit():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    db = S()
    owner = User(telegram_id=1, balance=0)
    db.add(owner); db.flush()
    for i in range(1, 13):
        buyer = User(telegram_id=100+i, referrer_id=owner.id, balance=0)
        db.add(buyer); db.flush()
        order = Order(user_id=buyer.id, status='paid', items_total=Decimal('1000'), balance_used=0, delivery_total=0, total=1000)
        db.add(order); db.commit()
        assert referral_rate(db, owner) == min(i, 10)
        c = credit_referrer_for_paid_order(db, order)
        assert c.rate_percent == min(i, 10)
    assert Decimal(owner.balance) == Decimal('750.00')
