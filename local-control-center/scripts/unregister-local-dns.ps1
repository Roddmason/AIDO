param(
	[switch]$NoElevate
)

$ErrorActionPreference = "Stop"

$HostsPath = Join-Path $env:SystemRoot "System32\drivers\etc\hosts"
$DefaultHostName = "local-control-center.test"
$BeginMarker = "# BEGIN Local Control Center DNS"
$EndMarker = "# END Local Control Center DNS"

function Test-Administrator {
	$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
	$principal = [Security.Principal.WindowsPrincipal]::new($identity)
	return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Quote-Arg([string]$Value) {
	return '"' + ($Value -replace '"', '\"') + '"'
}

if (-not (Test-Administrator)) {
	if ($NoElevate) {
		throw "Administrator privileges are required to update $HostsPath."
	}

	$PowerShellPath = (Get-Command powershell).Source
	$self = $MyInvocation.MyCommand.Path
	$arguments = @(
		"-NoProfile",
		"-ExecutionPolicy",
		"Bypass",
		"-File",
		(Quote-Arg $self)
	)
	Start-Process -FilePath $PowerShellPath -ArgumentList ($arguments -join " ") -Verb RunAs -Wait
	return
}

if (-not (Test-Path -LiteralPath $HostsPath)) {
	Write-Host "Hosts file not found at $HostsPath."
	return
}

$current = Get-Content -LiteralPath $HostsPath -Raw
$escapedBegin = [regex]::Escape($BeginMarker)
$escapedEnd = [regex]::Escape($EndMarker)
$markerPattern = "(?s)\r?\n?$escapedBegin.*?$escapedEnd\r?\n?"
$newContent = [regex]::Replace($current, $markerPattern, [Environment]::NewLine).TrimEnd() + [Environment]::NewLine

if ($current.TrimEnd() -eq $newContent.TrimEnd()) {
	Write-Host "Local Control Center DNS block was not present."
} else {
	Set-Content -LiteralPath $HostsPath -Value $newContent -Encoding ascii
	Write-Host "Removed Local Control Center DNS block."
}

ipconfig /flushdns | Out-Null
Write-Host "Flushed Windows DNS resolver cache."
