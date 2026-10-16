# index_all.ps1: полная индексация всех баз (split -> parallel x3 -> merge -> relations)
$ErrorActionPreference = "Stop"
$py = if (Test-Path .\.venv\Scripts\python.exe) { ".\.venv\Scripts\python.exe" } else { "python" }
& $py indexer\parallel.py split
foreach ($i in 1..3) {
  Start-Process -FilePath $py -ArgumentList "indexer\parallel.py run $i" -WindowStyle Hidden `
    -RedirectStandardOutput "parallel_$i.out" -RedirectStandardError "parallel_$i.err"
}
Write-Host "3 процесса индексации запущены. Прогресс: Get-Content index_part1.log -Tail 5"
Write-Host "После завершения всех: scripts\merge.ps1"
