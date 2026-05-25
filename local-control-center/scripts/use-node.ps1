$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$nvmrcPath = Join-Path $repoRoot ".nvmrc"

if (-not (Test-Path -LiteralPath $nvmrcPath)) {
    throw "Missing .nvmrc at $nvmrcPath"
}

$nodeVersion = (Get-Content -Raw -LiteralPath $nvmrcPath).Trim()
if (-not $nodeVersion) {
    throw ".nvmrc is empty."
}

$nvmCommand = Get-Command nvm -ErrorAction SilentlyContinue
if (-not $nvmCommand) {
    throw "nvm is not available on PATH. Install nvm-windows, then rerun this script."
}

$installedVersions = nvm list | Out-String
if ($installedVersions -notmatch [regex]::Escape($nodeVersion)) {
    nvm install $nodeVersion
}

nvm use $nodeVersion

corepack enable
corepack pnpm@10.24.0 --version | Out-Null

Write-Host "Node runtime ready:"
node --version
npm --version
corepack pnpm@10.24.0 --version
