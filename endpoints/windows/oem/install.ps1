$ErrorActionPreference = 'Stop'
Set-TimeZone -Id 'Korea Standard Time'
Set-Service -Name W32Time -StartupType Automatic
Start-Service W32Time
$destination = 'C:\ProgramData\KT66'
New-Item -ItemType Directory -Force $destination | Out-Null
Copy-Item 'C:\OEM\endpoint.ps1' "$destination\endpoint.ps1" -Force
if (-not (Get-NetFirewallRule -Name 'KT66-Health' -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -Name 'KT66-Health' -DisplayName 'KT66 read-only endpoint health' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8080 | Out-Null
}
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\ProgramData\KT66\endpoint.ps1'
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'KT66 Endpoint Health' -Action $action -Trigger $trigger -Settings $settings -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
Start-ScheduledTask -TaskName 'KT66 Endpoint Health'
Write-Output 'KT66 endpoint installed: read-only GET /health on port 8080.'
