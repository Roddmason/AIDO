param(
    [string]$BaseUrl = "http://127.0.0.1:4310",
    [string]$CollectorEndpoint = "http://127.0.0.1:4318",
    [switch]$StartCollector
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:AIDO_OTEL_SMOKE -ne "1") {
    Write-Host "Skip optional OpenTelemetry exporter smoke. Set AIDO_OTEL_SMOKE=1 to run."
    exit 0
}

if ($StartCollector) {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $docker) {
        Write-Host "Skip OTEL collector container startup because docker is not available."
        exit 0
    }
    $existing = docker ps --filter "name=aido-otel-smoke" --format "{{.Names}}"
    if (-not $existing) {
        docker run --rm -d --name aido-otel-smoke -p 4318:4318 otel/opentelemetry-collector-contrib:0.114.0 | Out-Null
        Start-Sleep -Seconds 3
    }
}

$env:AIDO_OTEL_EXPORTER = "otlp_http"
$env:AIDO_OTEL_ENDPOINT = $CollectorEndpoint

$status = Invoke-RestMethod -Method "GET" -Uri "$BaseUrl/api/v1/telemetry/status" -Headers @{ Accept = "application/json" }
if ($status.external.enabled -ne $true -or $status.external.mode -ne "otlp_http") {
    throw "The running control center is not configured for AIDO_OTEL_EXPORTER=otlp_http. Start it with the OTEL env vars before running this smoke."
}

Invoke-RestMethod -Method "GET" -Uri "$BaseUrl/healthz" -Headers @{ Accept = "application/json" } | Out-Null
Write-Host "OpenTelemetry exporter smoke completed against $CollectorEndpoint."
exit 0
