---
name: office-docs
description: Создание и проверка офисных документов (.docx) через python-docx. Используй ВСЕГДА когда нужно сохранить результат в файл Word — генерация, стили, таблицы, обязательная проверка чтением после записи.
---

# Работа с офисными документами (office-docs)

## Создание .docx (python-docx, установлен)

```python
from docx import Document
from docx.shared import Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH

doc = Document()
# базовый стиль
st = doc.styles["Normal"]
st.font.name = "Times New Roman"
st.font.size = Pt(12)

h = doc.add_heading("ЮРИДИЧЕСКАЯ СПРАВКА", level=0)
h.alignment = WD_ALIGN_PARAGRAPH.CENTER

doc.add_paragraph("Дата формирования: 20 августа 2026 г.")

doc.add_heading("1. Вопрос", level=1)
doc.add_paragraph("Текст вопроса пользователя.")

doc.add_heading("2. Источники", level=1)
for s in sources:
    doc.add_paragraph(s, style="List Number")

doc.add_heading("3. Основная часть", level=1)
doc.add_paragraph("Текст со ссылками на нормы.")

doc.save(r"Документы\имя_файла.docx")
```

## Правила

1. Имя файла: латиница, дата, краткая тема: `spravka_progul_2026-08-20.docx`.
2. Шрифт Times New Roman 12, заголовки — тем же шрифтом, поля 2 см.
3. Таблицы (если нужны): `doc.add_table(rows, cols, style="Table Grid")`.
4. Обязательная проверка после записи: открыть файл заново через
   `Document(path)`, убедиться, что заголовки и ключевые разделы на месте,
   нет пустых разделов и битых строк.
5. Если раздел пустой — не включай его в документ вообще.
6. Проверка результата — обязательна: после сохранения перечитай файл
   и сверь, что содержимое совпадает с задуманным.
