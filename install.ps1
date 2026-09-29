<#
.SYNOPSIS
    SRE-Hub Installation Script for Windows (PowerShell)
.DESCRIPTION
    Creates a Python virtual environment (.venv) and installs dependencies.
#>

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "🛠️  Setting up SRE-Hub in: $ScriptDir" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

# Check Python
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "❌ Error: python is not installed or not in PATH."
}

# Create virtual environment if missing
if (-not (Test-Path ".venv")) {
    Write-Host "📦 Creating virtual environment (.venv)..." -ForegroundColor Yellow
    python -m venv .venv
} else {
    Write-Host "✅ Virtual environment (.venv) already exists." -ForegroundColor Green
}

# Activate and install dependencies
Write-Host "🔄 Activating .venv and installing requirements..." -ForegroundColor Yellow
& "$ScriptDir\.venv\Scripts\python.exe" -m pip install --upgrade pip
& "$ScriptDir\.venv\Scripts\python.exe" -m pip install -r requirements.txt

# Ensure state directories exist
Write-Host "📁 Initializing local state directories..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path "state\maps", "state\logs", "state\digests", "state\bundles" | Out-Null

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "🎉 Setup completed successfully!" -ForegroundColor Green
Write-Host "👉 Run '.\start.ps1' to launch SRE-Hub." -ForegroundColor Yellow
Write-Host "==================================================" -ForegroundColor Cyan
