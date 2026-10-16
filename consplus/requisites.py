"""Извлечение реквизитов из шапки текста документа (DT2).

Типовая шапка:
    ОРГАН (uppercase)
    ВИД (uppercase: УКАЗ, ПОСТАНОВЛЕНИЕ, ФЕДЕРАЛЬНЫЙ ЗАКОН...)
    от D месяц YYYY г. N XXX  /  от D месяц YYYY г. по делу N XXX
    НАЗВАНИЕ (uppercase, может быть несколько строк)
    текст...
"""
import re

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11,
    "декабря": 12,
}

RE_DATE = re.compile(r"от (\d{1,2}) ([а-яё]+) (\d{4})")
RE_DATE_NUM = re.compile(r"от (\d{2})\.(\d{2})\.(\d{4})")
RE_NUM = re.compile(r"(?:по делу\s+)?[N№]\s*([^\s,;()]+)")
# частые виды документов (строка шапки целиком)
KIND_EXACT = {
    "УКАЗ", "ПОСТАНОВЛЕНИЕ", "РАСПОРЯЖЕНИЕ", "ПРИКАЗ", "РЕШЕНИЕ",
    "ОПРЕДЕЛЕНИЕ", "ЗАЯВЛЕНИЕ", "ФЕДЕРАЛЬНЫЙ ЗАКОН", "ЗАКОН",
    "ФЕДЕРАЛЬНЫЙ КОНСТИТУЦИОННЫЙ ЗАКОН", "ИНСТРУКЦИЯ", "ПОЛОЖЕНИЕ",
    "РЕГЛАМЕНТ", "ПИСЬМО", "ИНФОРМАЦИЯ", "ОБЗОР", "КОНВЕНЦИЯ",
    "ДОГОВОР", "СОГЛАШЕНИЕ", "ПРОТОКОЛ", "РЕГИСТРАЦИЯ", "ВЫПИСКА",
    "СПРАВКА", "ЗАКЛЮЧЕНИЕ", "АКТ", "ДОКЛАД", "УВЕДОМЛЕНИЕ",
    "КОНСТИТУЦИЯ", "КОДЕКС", "УСТАВ", "ПРАВИЛА", "ТАРИФНЫЙ ПЛАН",
}
KIND_PREFIXES = ("ПОСТАНОВЛЕНИЕ ПЛЕНУМА", "РЕШЕНИЕ ", "ОПРЕДЕЛЕНИЕ ")


def _is_kind(s: str) -> bool:
    if s in KIND_EXACT:
        return True
    if any(s.startswith(p) for p in KIND_PREFIXES):
        return True
    return False


def _is_upper_line(s: str) -> bool:
    letters = [c for c in s if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.9


def parse_requisites(text: str) -> dict:
    lines = [l.strip() for l in text.split("\n") if l.strip()][:10]
    out = {"agency": "", "kind": "", "date": None, "number": "", "title": ""}
    if not lines:
        return out

    head = " ".join(lines[:8])
    m = RE_DATE.search(head)
    if m:
        mon = MONTHS.get(m.group(2))
        if mon:
            out["date"] = f"{m.group(3)}-{mon:02d}-{int(m.group(1)):02d}"
    else:
        m2 = RE_DATE_NUM.search(head)
        if m2:
            out["date"] = f"{m2.group(3)}-{m2.group(2)}-{m2.group(1)}"
    n = RE_NUM.search(head)
    if n:
        out["number"] = n.group(1).strip(".")

    # орган: uppercase-строки до строки вида документа
    kind_idx = None
    for i, l in enumerate(lines[:5]):
        if RE_DATE.search(l) or RE_DATE_NUM.search(l):
            break
        if _is_upper_line(l) and _is_kind(l):
            kind_idx = i
            break
    if kind_idx is not None:
        out["kind"] = lines[kind_idx]
        out["agency"] = " ".join(lines[:kind_idx])
    elif lines and _is_upper_line(lines[0]):
        out["agency"] = lines[0]
        # fallback: вид = вторая строка, если короткая uppercase
        if len(lines) > 1 and _is_upper_line(lines[1]) and len(lines[1]) < 50 \
                and not RE_DATE.search(lines[1]) and not RE_NUM.search(lines[1]):
            out["kind"] = lines[1]

    # название: uppercase-строки после строки с датой
    title_parts = []
    started = False
    for l in lines:
        if RE_DATE.search(l) or RE_DATE_NUM.search(l):
            started = True
            continue
        if not started:
            continue
        if _is_upper_line(l):
            title_parts.append(l)
        else:
            break
    out["title"] = " ".join(title_parts)
    # нет реквизитов (путеводители, консультации): первая uppercase-строка — название
    if not out["date"] and not out["number"]:
        title2 = []
        for l in lines:
            if _is_upper_line(l) and len(l) > 3:
                title2.append(l)
            elif title2:
                break
        if title2:
            out["title"] = " ".join(title2)
            if out["kind"] and out["kind"] in out["title"]:
                out["kind"] = ""
        elif lines:
            out["title"] = lines[0][:200]
    return out


def normalize_number(s: str) -> str:
    return s.lower().replace("–", "-").replace(" ", "").strip(".")
