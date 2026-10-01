"""Lavka: actual carton packing, one bundled shipping place, order snapshots."""
import itertools
import json
import math
import re
import time
from fastapi import HTTPException

BOXES = ((30, 10, 10), (30, 20, 10), (30, 20, 20))
DEFAULT_CONFIG = {"wall_mm": 3, "tape_g": None, "box_weights_g": [None, None, None]}


def ensure_storage(db):
    from ..models import PackingData
    if not db.info.get("packing_storage"):
        PackingData.__table__.create(bind=db.connection(), checkfirst=True)
        db.info["packing_storage"] = True


def get_record(db, key):
    from ..models import PackingData
    ensure_storage(db)
    row = db.get(PackingData, key)
    return json.loads(row.value) if row else None


def put_record(db, key, value):
    from ..models import PackingData
    ensure_storage(db)
    row = db.get(PackingData, key)
    if row is None:
        row = PackingData(key=key, value="")
        db.add(row)
    row.value = json.dumps(value, ensure_ascii=False)
    db.info.pop("packing_product_flags", None)


def product_flags(db, product_id):
    from ..models import PackingData
    ensure_storage(db)
    if "packing_product_flags" not in db.info:
        db.info["packing_product_flags"] = {
            row.key: json.loads(row.value)
            for row in db.query(PackingData).filter(PackingData.key.like("p:%")).all()
        }
    return db.info["packing_product_flags"].get("p:" + str(product_id), {})


def product_meta(p):
    from sqlalchemy.orm import object_session
    db = object_session(p)
    flags = product_flags(db, p.id) if db else {}
    return {"shipping_ready": bool(flags.get("ready")),
            "allow_rotation": bool(flags.get("allow_rotation", True))}


def save_product_meta(db, p, body):
    db.flush()
    put_record(db, "p:" + str(p.id), {
        "ready": bool(body.shipping_ready),
        "allow_rotation": bool(body.allow_rotation),
    })


def validate_config(value):
    def number(v, label, maximum, nullable=False):
        if v is None and nullable:
            return None
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= maximum:
            raise HTTPException(400, label + ": укажите целое неотрицательное число")
        return v
    weights = value.get("box_weights_g", [])
    if not isinstance(weights, list) or len(weights) != 3:
        raise HTTPException(400, "Нужен вес трёх размеров коробок")
    return {
        "wall_mm": number(value.get("wall_mm"), "Толщина картона", 20),
        "tape_g": number(value.get("tape_g"), "Вес скотча", 10000, True),
        "box_weights_g": [number(x, "Вес коробки", 10000, True) for x in weights],
    }


def config_for(db):
    return validate_config(get_record(db, "config") or dict(DEFAULT_CONFIG))


def orientations(dims, rotate):
    return sorted(set(itertools.permutations(dims))) if rotate else sorted({dims, (dims[1], dims[0], dims[2])})


def contains(a, b):
    return all(a[i] <= b[i] and b[i] + b[i + 3] <= a[i] + a[i + 3] for i in range(3))


def split_spaces(spaces, placed):
    result = []
    for f in spaces:
        if any(placed[i] >= f[i] + f[i + 3] or placed[i] + placed[i + 3] <= f[i] for i in range(3)):
            result.append(f)
            continue
        for axis in range(3):
            if placed[axis] > f[axis]:
                part = list(f)
                part[axis + 3] = placed[axis] - f[axis]
                result.append(tuple(part))
            edge = placed[axis] + placed[axis + 3]
            if edge < f[axis] + f[axis + 3]:
                part = list(f)
                part[axis] = edge
                part[axis + 3] = f[axis] + f[axis + 3] - edge
                result.append(tuple(part))
    result = sorted(set(result))
    return [f for i, f in enumerate(result) if not any(i != j and contains(g, f) for j, g in enumerate(result))]


def fit_box(units, dims, strategy, deadline):
    keys = (
        lambda u: (math.prod(u["dims"]), max(u["dims"])),
        lambda u: (max(u["dims"]), math.prod(u["dims"])),
        lambda u: (u["dims"][2], math.prod(u["dims"])),
    )
    spaces = [(0, 0, 0, *dims)]
    placed = []
    for step, unit in enumerate(sorted(units, key=keys[strategy], reverse=True)):
        if step % 64 == 0 and time.monotonic() > deadline:
            raise HTTPException(400, "Для этой большой корзины нужен индивидуальный расчёт упаковки")
        if not spaces:
            break
        choices = []
        for f in spaces:
            for shape in orientations(unit["dims"], unit["rotate"]):
                if all(shape[i] <= f[i + 3] for i in range(3)):
                    score = (math.prod(f[3:]) - math.prod(shape), f[2], f[1], f[0], shape)
                    choices.append((score, (*f[:3], *shape)))
        if not choices:
            continue
        _, position = min(choices)
        placed.append({"unit": unit["serial"], "product_id": unit["product_id"],
                       "position_cm": list(position[:3]), "size_cm": list(position[3:])})
        spaces = split_spaces(spaces, position)
    return placed


def bundle(boxes, wall_mm):
    # Boxes remain closed; their contents never span cardboard partitions.
    rectangles = [(i, b["inner_cm"][1] * 10 + 2 * wall_mm,
                   b["inner_cm"][2] * 10 + 2 * wall_mm) for i, b in enumerate(boxes)]
    rectangles.sort(key=lambda r: (r[2], r[1]), reverse=True)
    widths = {max(r[1] for r in rectangles), sum(r[1] for r in rectangles)}
    widths.update(sum(r[1] for r in rectangles[:k]) for k in range(1, min(len(rectangles), 80) + 1))
    widths.add(max(max(r[1] for r in rectangles), math.ceil(math.sqrt(sum(w*h for _, w, h in rectangles)))))
    best = None
    for limit in sorted(widths):
        y = z = row_height = used_width = 0
        positions = {}
        for i, width, height in rectangles:
            if y + width > limit:
                z += row_height
                y = row_height = 0
            positions[i] = [0, y, z]
            y += width
            row_height = max(row_height, height)
            used_width = max(used_width, y)
        height = z + row_height
        dims_mm = (300 + 2 * wall_mm, used_width, height)
        score = (used_width * height, used_width + height, max(used_width, height))
        if best is None or score < best[0]:
            best = (score, dims_mm, positions)
    for i, b in enumerate(boxes):
        b["bundle_position_mm"] = best[2][i]
        b["outer_mm"] = [x * 10 + 2 * wall_mm for x in b["inner_cm"]]
    return {key: math.ceil(size / 10) for key, size in zip(("length", "width", "height"), best[1])}


def pack_items(items, config):
    config = validate_config(config)
    units = []
    for item in items:
        dims = tuple(item[k] for k in ("length_cm", "width_cm", "height_cm"))
        if any(isinstance(x, bool) or not isinstance(x, int) or x <= 0 for x in (*dims, item["weight_g"], item["qty"])):
            raise HTTPException(400, "Укажите размеры и вес: " + item["name"])
        if not any(all(a <= b for a, b in zip(shape, BOXES[-1])) for shape in orientations(dims, item["allow_rotation"])):
            raise HTTPException(400, "Товар не помещается в имеющиеся коробки: " + item["name"])
        for _ in range(item["qty"]):
            units.append({"serial": len(units), "product_id": item["product_id"],
                          "dims": dims, "rotate": item["allow_rotation"]})
    if not units:
        raise HTTPException(400, "Корзина пуста")
    if len(units) > 3000:
        raise HTTPException(400, "Для такого количества нужен индивидуальный расчёт упаковки")
    boxes = []
    deadline = time.monotonic() + 2
    while units:
        if time.monotonic() > deadline:
            raise HTTPException(400, "Для этой большой корзины нужен индивидуальный расчёт упаковки")
        candidates = []
        for index, dims in enumerate(BOXES):
            for strategy in range(3):
                placements = fit_box(units, dims, strategy, deadline)
                if placements:
                    candidates.append((index, placements))
            full = [p for i, p in candidates if i == index and len(p) == len(units)]
            if full:
                selected = (index, full[0])
                break
        else:
            selected = max(candidates, key=lambda c: (len(c[1]), -math.prod(BOXES[c[0]]))) if candidates else None
        if selected is None:
            raise HTTPException(400, "Не удалось разместить товары в коробках")
        index, placements = selected
        tare = config["box_weights_g"][index]
        if tare is None:
            raise HTTPException(400, "В админке товара откройте «Упаковка» и укажите вес коробки " + " × ".join(map(str, BOXES[index])))
        boxes.append({"box_type": index, "inner_cm": list(BOXES[index]),
                      "tare_g": tare, "placements": placements})
        assigned = {p["unit"] for p in placements}
        units = [u for u in units if u["serial"] not in assigned]
    if config["tape_g"] is None:
        raise HTTPException(400, "Укажите вес скотча и внешней упаковки на одну коробку в разделе «Упаковка»")
    package = bundle(boxes, config["wall_mm"])
    package["weight"] = sum(i["weight_g"] * i["qty"] for i in items) + sum(b["tare_g"] for b in boxes) + config["tape_g"] * len(boxes)
    return {"version": 1, "package": package, "boxes": boxes, "items": items,
            "config": config, "places": 1}


def product_order_name(p):
    name = str(getattr(p, "name", "") or "").strip()
    raw = str(getattr(p, "description", "") or "")
    m = re.search(r"\[\[(?:LV4|VARIANT):([^|\]]+)\|([0-9.]+)\|(g|ml|pcs)\]\]", raw)
    if not m:
        return name
    amount = float(m.group(2))
    unit = m.group(3)
    def num(v):
        return str(int(v)) if float(v).is_integer() else ("%g" % v).replace(".", ",")
    if unit == "ml" and amount >= 1000:
        label = num(amount / 1000) + " л"
    elif unit == "g" and amount >= 1000:
        label = num(amount / 1000) + " кг"
    else:
        label = num(amount) + " " + {"g": "г", "ml": "мл", "pcs": "шт."}[unit]
    suffix = " — " + label
    return name if name.endswith(suffix) else name + suffix

def plan_for(db, lines):
    items = []
    for p, qty in lines:
        flags = product_flags(db, p.id)
        if not flags.get("ready"):
            raise HTTPException(400, "Для расчёта доставки заполните размеры и вес фасовки: " + p.name)
        items.append({"product_id": p.id, "name": product_order_name(p),
                      "qty": int(qty), "cost": float(p.price),
                      "weight_g": int(p.weight_g), "length_cm": int(p.length_cm),
                      "width_cm": int(p.width_cm), "height_cm": int(p.height_cm),
                      "allow_rotation": bool(flags.get("allow_rotation", True))})
    return pack_items(items, config_for(db))


def cdek_packages(plan):
    return [dict(plan["package"])]


def shipment_package(plan, order_id):
    return {**plan["package"], "number": str(order_id) + "-1", "items": [
        {"name": i["name"][:255], "ware_key": str(i["product_id"]),
         "payment": {"value": 0}, "cost": i["cost"],
         "weight": i["weight_g"], "amount": i["qty"]} for i in plan["items"]]}


def saved_plan(db, order):
    plan = get_record(db, "o:" + str(order.id))
    if plan:
        return plan
    # Existing orders predate snapshots: do not guess for deleted products.
    from ..models import Product, OrderItem
    rows = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
    lines = []
    for row in rows:
        p = db.get(Product, row.product_id) if row.product_id else None
        if p is None:
            raise HTTPException(400, "Для старого заказа нужно вручную проверить упаковку: товар удалён")
        lines.append((p, row.qty))
    plan = plan_for(db, lines)
    put_record(db, "o:" + str(order.id), plan)
    return plan
