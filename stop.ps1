<#
.SYNOPSIS
AngelusH SRE Hub Stop Script (Windows / PowerShell)
Finds and stops the running Streamlit dashboard.
#>

Write-Host "🛑 Searching for running AngelusH SRE Hub instances..." -ForegroundColor Cyan

# Find processes running streamlit app.py
$processes = Get-WmiObject Win32_Process -Filter "CommandLine LIKE '%streamlit run app.py%'" | Where-Object { $_.CommandLine -notmatch "Get-WmiObject" }

if (-not $processes) {
    Write-Host "✅ No AngelusH SRE Hub instances found running." -ForegroundColor Green
    exit
}

foreach ($proc in $processes) {
    Write-Host "Stopping PID $($proc.ProcessId)..." -ForegroundColor Yellow
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 1

# Verify all are stopped
$remaining = Get-WmiObject Win32_Process -Filter "CommandLine LIKE '%streamlit run app.py%'" | Where-Object { $_.CommandLine -notmatch "Get-WmiObject" }

if ($remaining) {
    Write-Host "⚠️ Failed to stop all AngelusH SRE Hub instances." -ForegroundColor Red
} else {
    Write-Host "✅ AngelusH SRE Hub stopped successfully." -ForegroundColor Green
}
