# INSTALL.md — пошаговая установка для нового пользователя

## 1. Требования

- Windows 10/11, Python 3.13+ (проверка: `python --version`)
- 8+ ГБ свободной RAM, SSD/NVMe
- Локальная база КонсультантПлюс: папка, где лежат `BASE\`, `SYSTEM\` и т.д.
- Свободное место на диске: ~0.7 × объём папки `BASE\`

## 2. Установка

Откройте PowerShell в папке проекта и выполните:

```powershell
scripts\setup.ps1
```

Ожидаемый вывод: `OK: окружение готово. Запуск: scripts\run_api.ps1`
(создаётся `.venv`, ставятся зависимости; ~2-3 минуты)

## 3. Настройка пути к базе

```powershell
setx CONSPLUS_BASES "D:\путь\к\ConsultantPlus\BASE"
```

Переменная `CONSPLUS_BASES` должна указывать на папку `BASE` (внутри — подпапки LAW, EXP и т.д.).
Проверка: `dir $env:CONSPLUS_BASES\LAW` (в новом терминале).

## 4. Индексация

Полный прогон (все базы, ~2-3 часа, 3 параллельных процесса):

```powershell
scripts\index_all.ps1
```

Прогресс: `Get-Content index_part1.log -Tail 5` (и part2/part3).
Темп ~600-1000 страниц/с на процесс. Дождаться строки `[N/N] ... done` у всех трёх.

Затем мерж и построение связей:

```powershell
scripts\merge.ps1
```

Перед повторной сборкой остановите API. Скрипт создаёт `index_new.db`, строит в нём
связи, затем заменяет `index.db`. Предыдущий индекс сохраняется как
`index_backup_*.db`; файлы `index_part1..3.db` остаются на месте. Если сборка
прервалась, проверьте оставшийся `index_new.db` перед повторным запуском.
Результат: `index.db` (карточки + связи) и `index_part1..3.db` (FTS-индексы).

Одна база (быстро, для проверки): `python indexer\index_fast.py "$env:CONSPLUS_BASES\LAW" index.db`

## 5. Запуск API и проверка

```powershell
scripts\run_api.ps1
```

Открыть http://127.0.0.1:8741/docs (Swagger UI).
Smoke-проверка в другом терминале:

```powershell
(Invoke-RestMethod "http://127.0.0.1:8741/api/health").ok
(Invoke-RestMethod "http://127.0.0.1:8741/api/stats").docs
(Invoke-RestMethod "http://127.0.0.1:8741/api/search?q=налог").items.Count
```

MCP-сервер (для агентов): `python mcp_server\run.py` — сам поднимет API при необходимости.

## 6. Агент Mike (опционально)

```powershell
xcopy opencode-agent D:\МойПроект /E /I
cd D:\МойПроект
start.bat    # opencode, агент mike по умолчанию
```

В `opencode.json` шаблона поправьте путь к MCP (`...consplus-direct\mcp_server\run.py`).
Задача для проверки: «подготовь справку об основаниях увольнения за прогул по ТК РФ»
— в папке `Документы\` появится docx со ссылками на статьи.

## 7. Обновление базы КонсультантПлюс

После обновления базы оболочкой/файлами — повторите индексацию затронутых баз:
`python indexer\index_fast.py "$env:CONSPLUS_BASES\LAW" index.db`, затем
`python indexer\build_relations.py index.db`. Полный цикл (index_all+merge) — при массовом обновлении.
