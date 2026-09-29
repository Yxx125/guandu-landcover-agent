$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
docker compose ps
$port = if ($env:HTTP_PORT) { $env:HTTP_PORT } else { "8088" }
try { Invoke-WebRequest "http://127.0.0.1:$port/health/ready" -UseBasicParsing -TimeoutSec 10 | Select-Object StatusCode,Content } catch { Write-Host $_.Exception.Message }
