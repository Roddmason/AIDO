param(
	[string[]]$Hostnames = @("local-control-center.test", "lcc.test"),
	[string[]]$Addresses = @("127.0.0.1", "::1"),
	[switch]$NoElevate
)

$ErrorActionPreference = "Stop"

$HostsPath = Join-Path $env:SystemRoot "System32\drivers\etc\hosts"
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

function Assert-HostName([string]$HostName) {
	if ($HostName -notmatch '^[a-zA-Z0-9][a-zA-Z0-9.-]*[a-zA-Z0-9]$') {
		throw "Invalid hostname '$HostName'. Use plain DNS labels such as local-control-center.test."
	}
}

foreach ($hostname in $Hostnames) {
	Assert-HostName $hostname
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
		(Quote-Arg $self),
		"-Hostnames",
		(Quote-Arg ($Hostnames -join ",")),
		"-Addresses",
		(Quote-Arg ($Addresses -join ","))
	)

	Start-Process -FilePath $PowerShellPath -ArgumentList ($arguments -join " ") -Verb RunAs -Wait
	return
}

$Hostnames = @($Hostnames -join "," -split "," | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim().ToLowerInvariant() })
$Addresses = @($Addresses -join "," -split "," | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() })

$desiredLines = New-Object System.Collections.Generic.List[string]
$desiredLines.Add($BeginMarker)
foreach ($address in $Addresses) {
	foreach ($hostname in $Hostnames) {
		$desiredLines.Add("$address`t$hostname")
	}
}
$desiredLines.Add($EndMarker)
$desiredBlock = $desiredLines -join [Environment]::NewLine

$current = if (Test-Path -LiteralPath $HostsPath) {
	Get-Content -LiteralPath $HostsPath -Raw
} else {
	""
}

$escapedBegin = [regex]::Escape($BeginMarker)
$escapedEnd = [regex]::Escape($EndMarker)
$markerPattern = "(?s)\r?\n?$escapedBegin.*?$escapedEnd\r?\n?"
$withoutExistingBlock = [regex]::Replace($current, $markerPattern, [Environment]::NewLine).TrimEnd()
$newContent = ($withoutExistingBlock + [Environment]::NewLine + [Environment]::NewLine + $desiredBlock + [Environment]::NewLine)

if ($current.TrimEnd() -eq $newContent.TrimEnd()) {
	Write-Host "Local Control Center DNS already registered:"
} else {
	Set-Content -LiteralPath $HostsPath -Value $newContent -Encoding ascii
	Write-Host "Registered Local Control Center DNS:"
}

foreach ($hostname in $Hostnames) {
	Write-Host "  http://$hostname`:4310"
}

ipconfig /flushdns | Out-Null
Write-Host "Flushed Windows DNS resolver cache."
