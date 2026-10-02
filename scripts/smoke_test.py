from pathlib import Path
from decimal import Decimal
from sqlalchemy import text

errors=[]

def ok(name, fn):
    try:
        value=fn()
        print("OK  ",name, "" if value is None else value)
    except Exception as e:
        errors.append((name,str(e)))
        print("FAIL",name,":",e)

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import User,Product,Order,OrderItem,PromoCode
from app.services import packing
from app.services.pochta import pochta
from app.services.cdek import cdek
from app.services.orderflow import verified_payment

ok("DEV_AUTH выключен", lambda: (_ for _ in ()).throw(RuntimeError("DEV_AUTH=true")) if settings.dev_auth else "false")

db=SessionLocal()
try:
    ok("PostgreSQL", lambda: db.execute(text("SELECT 1")).scalar())
    ok("Пользователи", lambda: db.query(User).count())
    ok("Товары", lambda: db.query(Product).count())
    ok("Заказы", lambda: db.query(Order).count())
    ok("Позиции заказов", lambda: db.query(OrderItem).count())
    ok("Промокоды", lambda: db.query(PromoCode).count())
finally:
    db.close()

routes={getattr(r,"path","") for r in app.routes}
required=[
"/api/payments/yookassa/webhook",
"/api/pochta/quote",
"/api/admin/orders",
"/api/admin/packing",
]
for route in required:
    ok("Маршрут "+route, lambda r=route: r if r in routes else (_ for _ in ()).throw(RuntimeError("нет маршрута")))

ok("Почта России настроена", lambda: pochta.configured or (_ for _ in ()).throw(RuntimeError("не настроена")))
ok("СДЭК настроен", lambda: cdek.configured or (_ for _ in ()).throw(RuntimeError("не настроен")))

def payment_test():
    class O:
        payment_id="smoke-payment"
        total=Decimal("123.45")
    good={"id":"smoke-payment","status":"succeeded","paid":True,"amount":{"currency":"RUB","value":"123.45"}}
    bad={"id":"smoke-payment","status":"succeeded","paid":True,"amount":{"currency":"RUB","value":"999.00"}}
    assert verified_payment(good,O()) is True
    try:
        verified_payment(bad,O())
        raise RuntimeError("не поймана неверная сумма")
    except ValueError:
        pass
    return "сумма и ID проверяются"
ok("Проверка платежа ЮKassa",payment_test)

def packing_test():
    cfg={"wall_mm":3,"tape_g":20,"box_weights_g":[100,150,200]}
    item={"product_id":999999,"name":"SMOKE","qty":1,"cost":100,"weight_g":500,"length_cm":20,"width_cm":10,"height_cm":10,"allow_rotation":True}
    plan=packing.pack_items([item],cfg)
    assert plan["package"]["weight"]>0
    assert plan["package"]["length"]>0
    return str(plan["package"])
ok("Расчёт упаковки",packing_test)

def stock_guard():
    s=Path("app/main.py").read_text()
    assert ".with_for_update()" in s
    assert "Недостаточно остатка" in s
    assert "max(0, product.stock - line.qty)" not in s
    return "FOR UPDATE"
ok("Защита остатков",stock_guard)

def backup():
    p=Path("scripts/backup_db.sh")
    assert p.exists() and p.stat().st_size>100
    return "скрипт backup_db.sh найден"
ok("Резервная копия БД",backup)

if errors:
    print("\nSMOKE_FAIL =",len(errors))
    for n,e in errors:
        print(" -",n,":",e)
    raise SystemExit(1)

print("\nALL_SMOKE_TESTS_OK")
