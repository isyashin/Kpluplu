# merge.ps1: собрать новый индекс, затем заменить действующий
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repo ".venv\Scripts\python.exe"
$py = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { "python" }
$staged = Join-Path $repo "index_new.db"
$active = Join-Path $repo "index.db"

& $py (Join-Path $repo "indexer\merge_parts.py") $repo
if ($LASTEXITCODE -ne 0) { throw "Сборка index_new.db завершилась с ошибкой" }
& $py (Join-Path $repo "indexer\build_relations.py") $staged
if ($LASTEXITCODE -ne 0) { throw "Построение связей завершилось с ошибкой" }

$backup = $null
if (Test-Path -LiteralPath $active) {
    $backup = Join-Path $repo ("index_backup_{0}.db" -f (Get-Date -Format "yyyyMMdd_HHmmssfff"))
    Move-Item -LiteralPath $active -Destination $backup
}
try {
    Move-Item -LiteralPath $staged -Destination $active
} catch {
    if ($backup -and (Test-Path -LiteralPath $backup)) {
        Move-Item -LiteralPath $backup -Destination $active
    }
    throw
}

Write-Host "OK: index.db собран. Запуск API: scripts\run_api.ps1"
if ($backup) { Write-Host "Предыдущий индекс сохранён: $backup" }
