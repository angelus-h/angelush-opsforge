<#
.SYNOPSIS
    AngelusH SRE Hub Launcher Script for Windows (PowerShell)
.DESCRIPTION
    Activates .venv and launches the Streamlit app.
#>

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

if (-not (Test-Path "$ScriptDir\.venv\Scripts\Activate.ps1")) {
    Write-Host "⚠️  Virtual environment not found. Running install.ps1 first..." -ForegroundColor Yellow
    & "$ScriptDir\install.ps1"
}

Write-Host "🚀 Starting AngelusH SRE Hub Cockpit..." -ForegroundColor Green
& "$ScriptDir\.venv\Scripts\streamlit.exe" run "$ScriptDir\app.py" $args
