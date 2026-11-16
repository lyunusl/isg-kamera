# Before installing, closes the open ISG Kamera panel in this folder: first with the panel's own shutdown request (Studio
# runs also stop cleanly), and if it does not close, by ending only the processes of this exe. The shutdown request only
# goes to ports these processes listen on. It uses /api/shutdown and falls back to /api/kapat (the path of panels up to
# v0.1.0, kept as an alias in newer panels). Exit code: 0 closed, 1 still open.
param([Parameter(Mandatory = $true)][string]$AppPath)
$ErrorActionPreference = 'SilentlyContinue'
$target = [IO.Path]::GetFullPath($AppPath)
function Get-PanelProcesses { @(Get-Process -Name IsgKamera -ErrorAction SilentlyContinue | Where-Object { $_.Path -and ($_.Path -ieq $target) }) }
$initial = Get-PanelProcesses
if ($initial.Count -eq 0) { exit 0 }
$ports = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object { $initial.Id -contains $_.OwningProcess } | Select-Object -ExpandProperty LocalPort -Unique)
foreach ($p in $ports) {
  try {
    Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Method Post -Uri "http://127.0.0.1:$p/api/shutdown" `
      -ContentType 'application/json' -Body '{}' | Out-Null
  } catch {
    try {
      Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Method Post -Uri "http://127.0.0.1:$p/api/kapat" `
        -ContentType 'application/json' -Body '{}' | Out-Null
    } catch {}
  }
}
for ($i = 0; $i -lt 40 -and (Get-PanelProcesses).Count -gt 0; $i++) { Start-Sleep -Milliseconds 500 }
foreach ($s in Get-PanelProcesses) { & taskkill.exe /PID $s.Id /T /F | Out-Null }
for ($i = 0; $i -lt 10 -and (Get-PanelProcesses).Count -gt 0; $i++) { Start-Sleep -Milliseconds 500 }
if ((Get-PanelProcesses).Count -gt 0) { exit 1 }
exit 0
