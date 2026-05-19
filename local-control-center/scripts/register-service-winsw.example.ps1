param(
	[string]$ServiceName = "LocalControlCenter",
	[string]$WinSwExe = ""
)

throw "Windows Service mode is intentionally not the default. Use Task Scheduler unless you have configured a dedicated user account, profile access, and CLI credentials. Provide a WinSW executable and service XML before enabling this path."
