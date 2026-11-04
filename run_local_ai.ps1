# Starts Lifeline with the local Ollama model, after checking everything it needs.
# Put this file in the folder that contains "backend", then run (from any folder):
#   powershell -ExecutionPolicy Bypass -File .\run_local_ai.ps1
# Optional: -Model qwen2.5:3b   -Port 8000
param([string]$Model = "qwen2.5:3b", [int]$Port = 8000)

$backend = Join-Path $PSScriptRoot "backend"
if (-not (Test-Path (Join-Path $backend "main.py"))) {
    Write-Host "Cannot find backend\main.py next to this script. Put run_local_ai.ps1 in the folder that contains the 'backend' folder." -ForegroundColor Red; exit 1
}

# 1. Python (tests each candidate, because Windows has a fake "python" shortcut that opens the Microsoft Store)
$py = $null
foreach ($c in @("py", "python", "python3")) {
    if (Get-Command $c -ErrorAction SilentlyContinue) {
        $v = & $c --version 2>&1
        if ($LASTEXITCODE -eq 0 -and "$v" -match "Python 3") { $py = $c; break }
    }
}
if (-not $py) {
    Write-Host "No working Python found (the 'python' on this PC is only the Microsoft Store shortcut)." -ForegroundColor Red
    Write-Host "Install Python 3.12 from python.org (tick 'Add python.exe to PATH') or run:  winget install Python.Python.3.12" -ForegroundColor Yellow
    Write-Host "Then close this window, open a new PowerShell and run this script again." -ForegroundColor Yellow
    exit 1
}
Write-Host "Using Python: $(& $py --version 2>&1)"

# 2. Packages
& $py -c "import fastapi, uvicorn, multipart" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing the required packages (one time)..." -ForegroundColor Yellow
    & $py -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
    & $py -c "import fastapi, uvicorn, multipart" 2>$null
    if ($LASTEXITCODE -ne 0) { Write-Host "Package install failed. Read the red text above and tell me what it says." -ForegroundColor Red; exit 1 }
}

# 3. Ollama running, model downloaded
try { $tags = Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 5 }
catch { Write-Host "Ollama is not answering on port 11434. Open the Ollama app (system tray), or run 'ollama serve' in another window." -ForegroundColor Red; exit 1 }
if (-not ($tags.models | Where-Object { $_.name -eq $Model })) {
    Write-Host "Model '$Model' is not downloaded. Run:  ollama pull $Model" -ForegroundColor Red; exit 1
}

# 4. Port free?
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Port $Port is already in use (an old Lifeline window?). Close it, or run with -Port 8001." -ForegroundColor Red; exit 1
}

$env:LIFELINE_LLM = "openai"
$env:LLM_BASE_URL = "http://localhost:11434/v1"
$env:LLM_MODEL = $Model
Set-Location $backend
Write-Host "Starting Lifeline on http://localhost:$Port using $Model. Press Ctrl+C to stop." -ForegroundColor Green
& $py -m uvicorn main:app --host 127.0.0.1 --port $Port --reload
