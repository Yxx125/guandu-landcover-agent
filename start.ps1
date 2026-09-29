$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "Created .env; fill it in and run again"; exit 1 }
docker compose config | Out-Null
docker compose up -d --build postgres neo4j geoserver
docker compose run --rm geoserver-init
docker compose run --rm graph-init
docker compose run --rm document-index-init
docker compose up -d --build backend frontend
$port = if ($env:HTTP_PORT) { $env:HTTP_PORT } else { "8088" }
for ($attempt = 1; $attempt -le 60; $attempt++) {
    try {
        $healthUrl = "http://127.0.0.1:{0}/health/ready" -f $port
        $r = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 5
        if ($r.StatusCode -eq 200) {
            Write-Host ("Deployment ready: http://127.0.0.1:{0}" -f $port)
            exit 0
        }
    } catch {
        Start-Sleep 5
    }
}
docker compose ps
docker compose logs --tail 100 backend
exit 1
