from pydantic import BaseModel, Field
from typing import Optional

class CategoryIn(BaseModel):
    name: str
    slug: str
    sort_order: int = 0
    active: bool = True

class ProductIn(BaseModel):
    category_id: Optional[int] = None
    name: str
    description: str = ''
    price: float = Field(ge=0)
    stock: int = Field(default=0, ge=0)
    image_url: str = ''
    weight_g: int = Field(default=200, ge=1)
    length_cm: int = Field(default=15, ge=1)
    width_cm: int = Field(default=20, ge=1)
    height_cm: int = Field(default=10, ge=1)
    active: bool = True

class CartItemIn(BaseModel):
    product_id: int
    qty: int = Field(ge=1, le=100)

class QuoteIn(BaseModel):
    items: list[CartItemIn]
    to_city_code: int
    delivery_point: Optional[str] = None

class OrderIn(BaseModel):
    items: list[CartItemIn]
    customer_name: str
    phone: str
    city_code: Optional[int] = None
    city_name: Optional[str] = None
    delivery_type: str = 'pickup'
    delivery_tariff_code: Optional[int] = None
    delivery_point: Optional[str] = None
    delivery_total: float = 0
    address: Optional[str] = None
    use_balance: float = Field(default=0, ge=0)

class SettingsPatch(BaseModel):
    values: dict[str, str]
