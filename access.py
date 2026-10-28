import sqlite3

import config
import db
import demo

PROD_SALON = 1


def salon_for(tg_id: int) -> int:
    if not config.DEMO_MODE:
        return PROD_SALON
    sid = db.salon_by_owner(tg_id)
    if sid is not None:
        return sid
    try:
        sid = db.create_salon(tg_id)
    except sqlite3.IntegrityError:  # concurrent /start created it first; they seed it
        return db.salon_by_owner(tg_id)
    demo.seed(sid)
    return sid


def admins_of(salon_id: int) -> set[int]:
    if not config.DEMO_MODE:
        return set(config.ADMIN_IDS)
    owner = db._one("SELECT owner_tg_id FROM salons WHERE id=?", (salon_id,))
    return {owner["owner_tg_id"]} if owner and owner["owner_tg_id"] is not None else set()


def is_admin(tg_id: int, salon_id: int) -> bool:
    return tg_id in admins_of(salon_id)
