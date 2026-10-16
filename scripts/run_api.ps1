# run_api.ps1: запуск Search API
$py = if (Test-Path .\.venv\Scripts\python.exe) { ".\.venv\Scripts\python.exe" } else { "python" }
& $py -m uvicorn api.main:app --host 127.0.0.1 --port 8741
