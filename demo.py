import db

_CATALOG = {
    "Анна": [("Стрижка", 60, 500), ("Окрашивание", 120, 1500), ("Укладка", 30, 300)],
    "Игорь": [("Мужская стрижка", 60, 400), ("Борода", 30, 250)],
}


def seed(salon_id: int) -> None:
    for name, services in _CATALOG.items():
        m = db.add_master(salon_id, name)
        for sv in services:
            db.add_service(salon_id, m, *sv)
        for wd in range(5):
            db.set_hours(salon_id, m, wd, "10:00", "19:00")
        db.set_hours(salon_id, m, 5, "10:00", "16:00")
