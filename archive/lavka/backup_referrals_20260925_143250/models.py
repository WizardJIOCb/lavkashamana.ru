from datetime import datetime
from decimal import Decimal
from sqlalchemy import String, Integer, BigInteger, Boolean, DateTime, ForeignKey, Numeric, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base

class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    city_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    city_name: Mapped[str | None] = mapped_column(String(180), nullable=True)
    delivery_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    delivery_point: Mapped[str | None] = mapped_column(String(80), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    referrer_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True, index=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Category(Base):
    __tablename__ = 'categories'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    slug: Mapped[str] = mapped_column(String(160), unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class Product(Base):
    __tablename__ = 'products'
    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey('categories.id'), nullable=True)
    name: Mapped[str] = mapped_column(String(220))
    description: Mapped[str] = mapped_column(Text, default='')
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    stock: Mapped[int] = mapped_column(Integer, default=0)
    image_url: Mapped[str] = mapped_column(Text, default='')
    weight_g: Mapped[int] = mapped_column(Integer, default=200)
    length_cm: Mapped[int] = mapped_column(Integer, default=15)
    width_cm: Mapped[int] = mapped_column(Integer, default=20)
    height_cm: Mapped[int] = mapped_column(Integer, default=10)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    category = relationship('Category')

class Order(Base):
    __tablename__ = 'orders'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    status: Mapped[str] = mapped_column(String(40), default='new', index=True)
    items_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    balance_used: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    delivery_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    payment_provider: Mapped[str] = mapped_column(String(40), default='mock')
    payment_id: Mapped[str | None] = mapped_column(String(160), nullable=True, unique=True)
    payment_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_type: Mapped[str] = mapped_column(String(40), default='pickup')
    delivery_tariff_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delivery_point: Mapped[str | None] = mapped_column(String(80), nullable=True)
    city_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    city_name: Mapped[str | None] = mapped_column(String(180), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(180), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cdek_order_uuid: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

class OrderItem(Base):
    __tablename__ = 'order_items'
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('orders.id'), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey('products.id'), nullable=True)
    name: Mapped[str] = mapped_column(String(220))
    qty: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2))

class ReferralCredit(Base):
    __tablename__ = 'referral_credits'
    __table_args__ = (UniqueConstraint('source_order_id', name='uq_referral_credit_source_order'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    referrer_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    referred_user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    source_order_id: Mapped[int] = mapped_column(ForeignKey('orders.id'))
    rate_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    base_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Setting(Base):
    __tablename__ = 'settings'
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default='')
