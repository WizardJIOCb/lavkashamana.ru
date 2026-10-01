import asyncio
import inspect
import functools
import time
from contextlib import contextmanager, asynccontextmanager
from decimal import Decimal
from fastapi import Depends, HTTPException
from sqlalchemy import text


def verified_payment(payment, order):
    if not isinstance(payment, dict) or payment.get("id") != order.payment_id:
        raise ValueError("ЮKassa вернула другой платёж")
    amount = payment.get("amount") or {}
    if amount.get("currency") != "RUB" or Decimal(str(amount.get("value"))) != Decimal(str(order.total)):
        raise ValueError("Сумма платежа не совпадает с заказом")
    return payment.get("status") == "succeeded" and payment.get("paid") is True


def install(n):
    app, packing, Order, Item = n["app"], n["packing"], n["Order"], n["OrderItem"]
    get_db, admin = n["get_db"], n["require_admin"]
    original_paid, original_ship = n["mark_paid"], n["create_cdek_shipment"]

    def record(db, order):
        prefix = (
            "pochta:"
            if getattr(order, "delivery_provider", None) == "pochta"
            else "ship:"
        )
        return packing.get_record(db, prefix + str(order.id)) or {}

    def save(db, order, value):
        packing.put_record(db, "ship:" + str(order.id), value)
        db.commit()

    @contextmanager
    def locked(db, order_id, group):
        with db.get_bind().connect() as connection:
            if connection.dialect.name != "postgresql":
                raise RuntimeError("Для защиты от повторной отправки требуется PostgreSQL")
            params = {"g": group, "i": int(order_id)}
            if not connection.execute(text("SELECT pg_try_advisory_lock(:g, :i)"), params).scalar():
                raise HTTPException(409, "Заказ уже обрабатывается; обновите карточку")
            try:
                yield
            finally:
                connection.execute(text("SELECT pg_advisory_unlock(:g, :i)"), params)

    async def office(db, order, data):
        if order.delivery_provider != "cdek" or not order.delivery_point or not order.city_code:
            return data
        if data.get("point_address"):
            return data
        try:
            points = await n["cdek"].delivery_points(int(order.city_code))
            if isinstance(points, dict):
                points = points.get("delivery_points") or points.get("data") or []
            point = next((p for p in points if str(p.get("code")) == str(order.delivery_point)), None)
            if point:
                loc = point.get("location") or {}
                data.update(point_address=loc.get("address_full") or loc.get("address") or "", point_city=loc.get("city") or "")
                save(db, order, data)
        except Exception:
            db.rollback()
        return data

    async def submit(db, order, payload):
        data = await office(db, order, record(db, order))
        packages = payload.get("packages") or []
        data.update(state="sending", attempted_at=time.time(), payload={
            k: payload.get(k) for k in ("number", "tariff_code", "shipment_point", "recipient", "delivery_point", "to_location", "packages")
        }, error=None)
        save(db, order, data)
        try:
            result = await n["cdek"].create_order(payload)
        except Exception:
            db.rollback()
            data.update(state="uncertain", error="Нет подтверждённого ответа СДЭК. Проверьте LAVKA-" + str(order.id) + " в кабинете СДЭК перед повторной отправкой.")
            save(db, order, data)
            raise
        if not isinstance(result, dict):
            data.update(state="uncertain", error="Не удалось разобрать ответ СДЭК; проверьте заявку в кабинете перевозчика.")
            save(db, order, data)
            raise RuntimeError(data["error"])
        requests = result.get("requests") or []
        invalid = any(r.get("state") == "INVALID" or r.get("errors") for r in requests)
        uuid = (result.get("entity") or {}).get("uuid")
        data.update(state="rejected" if invalid else ("submitted" if uuid else "uncertain"), response=result)
        if invalid:
            data["error"] = str([r.get("errors") for r in requests if r.get("errors")])[:1000]
        elif not uuid:
            data["error"] = "СДЭК не вернул UUID. Проверьте заказ в кабинете перевозчика."
        else:
            order.cdek_order_uuid = uuid
        save(db, order, data)
        return result

    async def ship(db, order):
        if order.delivery_provider != "cdek" or order.cdek_order_uuid:
            return order.cdek_order_uuid
        if order.status in ("cancelled", "canceled", "cancelled_refunded", "refunded", "failed", "payment_failed"):
            raise HTTPException(409, "Отменённый или возвращённый заказ нельзя отправить")
        if not order.paid_at and order.status != "paid":
            raise HTTPException(409, "Отправление создаётся после подтверждения оплаты")
        with locked(db, order.id, 813008):
            db.refresh(order)
            if order.cdek_order_uuid:
                return order.cdek_order_uuid
            data = record(db, order)
            if data.get("state") in ("sending", "uncertain"):
                raise HTTPException(409, data.get("error") or "СДЭК ещё не подтвердил отправление; проверьте кабинет перевозчика")
            try:
                return await original_ship(db, order)
            except Exception as exc:
                db.rollback()
                data = record(db, order)
                if data.get("state") == "sending":
                    data.update(state="uncertain", error="Отправка прервана. Проверьте заявку в кабинете СДЭК.")
                    save(db, order, data)
                elif data.get("state") not in ("uncertain", "rejected"):
                    data.update(state="error", error=str(getattr(exc, "detail", exc))[:1000])
                    save(db, order, data)
                raise

    async def paid(db, order):
        with locked(db, order.id, 813007):
            db.refresh(order)
            result = None
            if not order.paid_at and order.status not in ("paid", "shipped", "delivered", "completed"):
                result = await original_paid(db, order)
            db.refresh(order)
            if order.delivery_provider == "cdek" and not order.cdek_order_uuid and order.paid_at:
                await ship(db, order)
            elif order.delivery_provider == "pochta" and order.paid_at:
                data = record(db, order)
                if data.get("state") not in ("submitted", "sending", "uncertain"):
                    creator = n.get("create_pochta_shipment")
                    if creator:
                        await creator(db, order)
            return result

    def detail(db, order):
        fields = ("id", "status", "total", "items_total", "delivery_total", "promo_discount", "balance_used", "customer_name", "phone", "email", "city_name", "city_code", "address", "postal_code", "delivery_provider", "delivery_type", "delivery_point", "delivery_tariff_code", "payment_provider", "payment_id", "paid_at", "created_at", "cdek_order_uuid")
        data = {key: getattr(order, key, None) for key in fields}
        data["items"] = [{key: getattr(row, key, None) for key in ("product_id", "name", "qty", "unit_price", "line_total")} for row in db.query(Item).filter(Item.order_id == order.id).all()]
        data["shipment"] = record(db, order)
        data["packing"] = packing.get_record(db, "o:" + str(order.id))
        return data

    def extend(value, db):
        rows = value if isinstance(value, list) else value.get("orders", []) if isinstance(value, dict) else []
        ids = [row.get("id") for row in rows if isinstance(row, dict)]
        orders = {o.id: o for o in db.query(Order).filter(Order.id.in_(ids)).all()} if ids else {}
        for row in rows:
            if isinstance(row, dict) and row.get("id") in orders:
                row.update(detail(db, orders[row["id"]]))
        return value

    for route in app.routes:
        if getattr(route, "path", "") in ("/api/admin/orders", "/api/admin/sales-overview") and "GET" in (getattr(route, "methods", set()) or set()):
            def wrapper(fn):
                @functools.wraps(fn)
                async def wrapped(*args, **kwargs):
                    result = fn(*args, **kwargs)
                    if inspect.isawaitable(result):
                        result = await result
                    return extend(result, kwargs["db"])
                return wrapped
            route.dependant.call = wrapper(route.dependant.call)

    @app.get("/api/admin/orders/{order_id}/delivery-details")
    async def details(order_id: int, user=Depends(admin), db=Depends(get_db)):
        order = db.get(Order, order_id)
        if not order:
            raise HTTPException(404, "Заказ не найден")
        await office(db, order, record(db, order))
        return detail(db, order)

    async def sync(db, order):
        if order.payment_id and order.payment_provider == "yookassa":
            payment = await n["get_gateway"]().fetch(order.payment_id)
            ok = verified_payment(payment, order)
            data = record(db, order)
            data["payment"] = {k: payment.get(k) for k in ("id", "status", "paid", "amount", "receipt_registration", "captured_at")}
            save(db, order, data)
            if ok and order.status in ("awaiting_payment", "pending", "created", "new"):
                await paid(db, order)
        db.refresh(order)
        if order.paid_at and order.status not in ("cancelled", "canceled", "cancelled_refunded", "refunded", "failed", "payment_failed"):
            if order.delivery_provider == "cdek" and not order.cdek_order_uuid:
                await ship(db, order)
            elif order.delivery_provider == "pochta":
                data = record(db, order)
                if data.get("state") not in ("submitted", "sending", "uncertain"):
                    creator = n.get("create_pochta_shipment")
                    if creator:
                        await creator(db, order)

    @app.post("/api/admin/orders/{order_id}/sync-delivery")
    async def sync_route(order_id: int, user=Depends(admin), db=Depends(get_db)):
        order = db.get(Order, order_id)
        if not order:
            raise HTTPException(404, "Заказ не найден")
        try:
            await sync(db, order)
        except HTTPException:
            raise
        except Exception as exc:
            db.rollback()
            raise HTTPException(502, "Не удалось обновить оплату или отправление: " + str(getattr(exc, "detail", exc))[:500])
        return detail(db, order)

    async def reconcile():
        while True:
            await asyncio.sleep(60)
            generator = None
            try:
                generator = get_db()
                db = next(generator)
                ids = [o.id for o in db.query(Order).filter(Order.status == "awaiting_payment", Order.payment_provider == "yookassa", Order.payment_id.isnot(None)).order_by(Order.id.desc()).limit(100).all()]
                for order_id in ids:
                    try:
                        await sync(db, db.get(Order, order_id))
                    except Exception as exc:
                        db.rollback()
                        print("LAVKA order sync", order_id, type(exc).__name__, flush=True)
            except Exception as exc:
                print("LAVKA payment sync", type(exc).__name__, flush=True)
            finally:
                if generator:
                    generator.close()

    original_lifespan = app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with original_lifespan(application) as state:
            task = asyncio.create_task(reconcile())
            try:
                yield state
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
    app.router.lifespan_context = lifespan

    n.update(mark_paid=paid, create_cdek_shipment=ship, _lavka_orderflow_submit=submit)
    roots = [r for r in app.router.routes if getattr(r, "path", None) == "" and r.__class__.__name__ == "Mount"]
    for root in roots:
        app.router.routes.remove(root)
        app.router.routes.append(root)
