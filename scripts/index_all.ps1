# index_all.ps1: полная индексация всех баз; ждёт завершения трёх групп
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repo ".venv\Scripts\python.exe"
$py = if (Test-Path -LiteralPath $venvPython) {
    $venvPython
} else {
    (Get-Command python -ErrorAction Stop).Source
}
foreach ($i in 1..3) {
    $part = Join-Path $repo "index_part$i.db"
    if (Test-Path -LiteralPath $part) {
        throw "Индекс уже существует: $part. Полную сборку запускайте в пустой папке индексов."
    }
}

Push-Location $repo
try {
    & $py indexer\parallel.py split
    if ($LASTEXITCODE -ne 0) { throw "Не удалось разбить базы на группы" }

    $processes = @()
    foreach ($i in 1..3) {
        $process = Start-Process -FilePath $py -ArgumentList "indexer\parallel.py run $i" `
            -WorkingDirectory $repo -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $repo "parallel_$i.out") `
            -RedirectStandardError (Join-Path $repo "parallel_$i.err")
        $processes += [pscustomobject]@{ Group = $i; Process = $process }
    }
    Write-Host "Индексация трёх групп идёт. Прогресс: Get-Content index_part1.log -Tail 5"
    $failed = @()
    foreach ($item in $processes) {
        $item.Process.WaitForExit()
        if ($item.Process.ExitCode -ne 0) { $failed += $item.Group }
    }
    if ($failed.Count -gt 0) {
        throw "Индексация не завершилась: группы $($failed -join ', '). Проверьте index_part*.log и parallel_*.err."
    }
    Write-Host "Все группы завершились успешно. Следующий шаг: .\scripts\merge.ps1"
} finally {
    Pop-Location
}
