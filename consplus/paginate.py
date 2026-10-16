"""Группировка страниц DT2 в документы: новая страница = новый документ,
если текст начинается с шапки (uppercase орган/вид + 'от <дата>')."""
import re

MONTHS_RE = "января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря"
RE_DATELINE = re.compile(rf"от \d{{1,2}} (?:{MONTHS_RE}) \d{{4}}|от \d{{2}}\.\d{{2}}\.\d{{4}}")

KIND_WORDS = (
    "УКАЗ", "ПОСТАНОВЛЕНИЕ", "РАСПОРЯЖЕНИЕ", "ПРИКАЗ", "РЕШЕНИЕ",
    "ОПРЕДЕЛЕНИЕ", "ФЕДЕРАЛЬНЫЙ ЗАКОН", "ЗАКОН", "ИНСТРУКЦИЯ",
    "ПОЛОЖЕНИЕ", "ПИСЬМО", "ИНФОРМАЦИЯ", "ОБЗОР", "КОНВЕНЦИЯ",
    "ДОГОВОР", "СОГЛАШЕНИЕ", "УКАЗАНИЕ", "РЕГЛАМЕНТ", "ВЫПИСКА",
    "ПРАВИЛА", "КОДЕКС", "КОНСТИТУЦИЯ", "УСТАВ",
)

DOC_START_PREFIXES = (
    "Федеральный закон от", "Закон РФ от", "Закон Российской Федерации от",
    "Гражданский кодекс", "Налоговый кодекс", "Трудовой кодекс",
    "Уголовный кодекс", "Кодекс ", "Вопрос:", "Ответ:",
)


def _upper_ratio(s: str) -> float:
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return 0.0
    return sum(c.isupper() for c in letters) / len(letters)


# страница-вставка: "|26 января 1996 года N 14-ФЗ\n|---..." — новый документ
RE_FRAGMENT_START = re.compile(
    rf"^\d{{1,2}} (?:{MONTHS_RE}) \d{{4}}\s*(?:года|г\.?)\s*N\s*\S", re.IGNORECASE)


def is_doc_start(text: str) -> bool:
    """True, если чанк начинает новый документ (шапка с реквизитами).

    Принимает текст в сыром виде (разделители \x00) или построчный.
    """
    if "\x00" in text:
        text = text.replace("\x00", "\n")
    lines = [l.strip() for l in text.split("\n") if l.strip()][:4]
    if not lines:
        return False
    first = lines[0]
    if RE_FRAGMENT_START.match(first):
        return True
    if first.startswith(DOC_START_PREFIXES):
        return True
    head = " ".join(lines)
    has_date = bool(RE_DATELINE.search(head))
    if not has_date:
        # путеводители/справочники: первая строка целиком uppercase — новый документ
        return _upper_ratio(first) > 0.9 and len(first) > 10 and not first[0].isdigit()
    # есть дата в шапке: первая строка должна быть uppercase (орган/вид)
    if _upper_ratio(first) > 0.9:
        return True
    # вид документа может быть первой строкой ("Федеральный закон" уже покрыт)
    if any(first.startswith(k) for k in KIND_WORDS):
        return True
    return False
