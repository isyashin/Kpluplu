# setup.ps1: создание venv и установка зависимостей
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$systemPython = (Get-Command python -ErrorAction Stop).Source
$venv = Join-Path $repo ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
& $systemPython -m venv $venv
if ($LASTEXITCODE -ne 0) { throw "Не удалось создать виртуальное окружение" }
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Не удалось обновить pip" }
& $venvPython -m pip install -r (Join-Path $repo "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Не удалось установить зависимости" }
Write-Host "OK: окружение готово. Запуск: scripts\run_api.ps1"
