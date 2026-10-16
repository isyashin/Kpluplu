"""MCP-сервер поверх Search API КонсультантПлюс.

Запуск stdio:  python mcp_server/server.py
Запуск HTTP:   MCP_TRANSPORT=streamable-http python mcp_server/server.py
"""
import json
import os
import sys
import urllib.parse
import urllib.request

from mcp.server.fastmcp import FastMCP

API = os.environ.get("CONSPLUS_API", "http://127.0.0.1:8741")

mcp = FastMCP(
    "consultantplus",
    instructions=(
        "Доступ к локальной копии баз КонсультантПлюс (законодательство РФ, "
        "судебная практика, консультации). Ищите документы через search_legislation "
        "или find_by_requisites, затем читайте полный текст через get_document_text. "
        "Статусы и связи получены из аннотаций автоматически; проверяйте их "
        "по тексту документа перед выводами."
    ),
)


def _get(path: str, **params):
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    with urllib.request.urlopen(f"{API}{path}?{qs}") as r:
        return json.loads(r.read().decode("utf-8"))


def _get_text(path: str) -> str:
    with urllib.request.urlopen(f"{API}{path}") as r:
        return r.read().decode("utf-8")


@mcp.tool()
def search_legislation(query: str, base: str | None = None, kind: str | None = None,
                       date_from: str | None = None, date_to: str | None = None,
                       limit: int = 10, page: int = 1) -> str:
    """Полнотекстовый поиск по базам КонсультантПлюс (морфология русского языка).

    query — свободный текст ("налог на имущество", "увольнение за прогул").
    base — ограничить базой (LAW — федеральное законодательство, KSOJ* — суды общей
    юрисдикции, RAPS* — арбитражная практика, QUEST — консультации; список — list_bases).
    kind — вид документа (УКАЗ, ПОСТАНОВЛЕНИЕ, ФЕДЕРАЛЬНЫЙ ЗАКОН...).
    page — номер страницы (с 1); limit — число карточек на странице (1–50).
    Результаты идут по рангу FTS, при равенстве — по ID индекса.
    Возвращает карточки с id для последующего get_document_text.
    """
    res = _get("/api/search", q=query, base=base, kind=kind,
               date_from=date_from, date_to=date_to,
               per_page=max(1, min(limit, 50)), page=max(1, page))
    items = res.get("items", [])
    if not items:
        return "Ничего не найдено."
    lines = []
    for it in items:
        lines.append(
            f"[{it['id']}] {it.get('kind') or ''} {it.get('number') or ''} "
            f"от {it.get('date') or '?'} — {it.get('title') or '(без названия)'}\n"
            f"    {it.get('agency') or ''} | {it.get('snippet', '')[:200]}"
        )
    return "\n\n".join(lines)


@mcp.tool()
def find_by_requisites(number: str, date: str | None = None,
                       base: str | None = None) -> str:
    """Точный поиск документа по реквизитам: номер (361, 44-ФЗ, А11-12476/2009)
    и дата принятия (YYYY-MM-DD). Сначала записи, где реквизиты найдены
    в начале текста; это эвристика, а не подтверждение редакции."""
    res = _get("/api/documents/requisites", number=number, date=date, base=base)
    items = res.get("items", [])
    if not items:
        return "Документ с такими реквизитами не найден."
    return "\n".join(
        f"[{d['id']}] {d.get('kind') or ''} N {d.get('number')} от {d.get('date')}"
        f" — {d.get('agency') or ''} {d.get('title') or ''}"
        f"{' [реквизиты в начале текста]' if d.get('header_match') else ''}\n"
        f"    Начало текста: {d.get('preview') or '(нет текста)'}"
        for d in items
    )


@mcp.tool()
def get_document(doc_id: int) -> str:
    """Карточка документа: реквизиты, источник публикации, статусная аннотация,
    список редакций."""
    return json.dumps(_get(f"/api/documents/{doc_id}"), ensure_ascii=False, indent=1)


@mcp.tool()
def get_document_text(doc_id: int, offset: int = 0,
                      max_chars: int | None = None) -> str:
    """Текст документа по id. Без дополнительных параметров — полный текст.
    Для длинных документов задайте offset и max_chars (до 20000 символов):
    ответ содержит фрагмент, next_offset и has_more.
    """
    full_text = _get_text(f"/api/documents/{doc_id}/text")
    if offset == 0 and max_chars is None:
        return full_text
    start = max(0, offset)
    size = max(1, min(max_chars if max_chars is not None else 10000, 20000))
    chunk = full_text[start:start + size]
    next_offset = start + len(chunk)
    has_more = next_offset < len(full_text)
    return json.dumps({
        "id": doc_id, "offset": start, "text": chunk,
        "next_offset": next_offset if has_more else None,
        "has_more": has_more, "total_chars": len(full_text),
    }, ensure_ascii=False)


@mcp.tool()
def get_document_status(doc_id: int) -> str:
    """Статус по аннотации заголовка; не заменяет проверку текста и редакции."""
    return json.dumps(_get(f"/api/documents/{doc_id}/status"),
                      ensure_ascii=False, indent=1)


@mcp.tool()
def get_related_documents(doc_id: int) -> str:
    """Связи из аннотаций: чем отменён/изменён (outgoing) и на какие документы
    влияет (incoming). Ответ содержит исходную аннотацию для проверки."""
    return json.dumps(_get(f"/api/documents/{doc_id}/relations"),
                      ensure_ascii=False, indent=1)


@mcp.tool()
def list_bases() -> str:
    """Список проиндексированных информационных банков (значения для base=)."""
    return json.dumps(_get("/api/bases"), ensure_ascii=False)


if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)
