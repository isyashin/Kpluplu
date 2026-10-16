# setup.ps1: создание venv и установка зависимостей
$ErrorActionPreference = "Stop"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\pip.exe install -r requirements.txt
Write-Host "OK: окружение готово. Запуск: scripts\run_api.ps1"
