import re


def parse_phone(text: str) -> str | None:
    cleaned = re.sub(r"[\s()\-]", "", text)
    return cleaned if re.fullmatch(r"\+?[0-9]{10,15}", cleaned) else None


def check_text(text: str | None) -> str | None:
    if text is None:
        return None
    text = text.strip()
    return text if 1 <= len(text) <= 100 else None


def parse_hours(text: str) -> tuple[str, str] | None:
    m = re.fullmatch(r"\s*([0-9]{1,2}):([0-9]{2})\s*-\s*([0-9]{1,2}):([0-9]{2})\s*", text)
    if not m:
        return None
    h1, m1, h2, m2 = map(int, m.groups())
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59 or (h1, m1) >= (h2, m2):
        return None
    return f"{h1:02d}:{m1:02d}", f"{h2:02d}:{m2:02d}"


def parse_price(text: str) -> int | None:
    cleaned = re.sub(r"\s", "", text)
    if not re.fullmatch(r"[0-9]{1,7}", cleaned):
        return None
    value = int(cleaned)
    return value if value <= 1_000_000 else None
