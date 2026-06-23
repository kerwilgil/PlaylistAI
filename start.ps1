$ErrorActionPreference = "Stop"

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath ".env") -and (Test-Path -LiteralPath ".env.example")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
    Write-Host "Se creo .env desde .env.example. Agrega tus keys para conectar Spotify e IA."
    Write-Host ""
}

Write-Host ""
Write-Host "PlaylistAI corriendo en http://127.0.0.1:5000"
Write-Host ""

if ($env:PLAYLISTAI_NO_BROWSER -ne "1") {
    Start-Job -ScriptBlock {
        for ($i = 0; $i -lt 30; $i++) {
            try {
                Invoke-WebRequest -Uri "http://127.0.0.1:5000" -UseBasicParsing -TimeoutSec 1 | Out-Null
                Start-Process "http://127.0.0.1:5000"
                break
            } catch {
                Start-Sleep -Seconds 1
            }
        }
    } | Out-Null
}

function Test-Cmd {
    param([string]$Command)
    cmd.exe /c "$Command --version >nul 2>nul"
    return $LASTEXITCODE -eq 0
}

if (Test-Cmd "uv") {
    cmd.exe /c "uv run --with-requirements requirements.txt python app.py"
    exit $LASTEXITCODE
}

Write-Host "uv no esta instalado. Usando entorno virtual local (.venv)..."
Write-Host ""

$pythonCommand = $null
if (Test-Cmd "py -3") {
    $pythonCommand = "py -3"
} elseif (Test-Cmd "python") {
    $pythonCommand = "python"
} elseif (Test-Cmd "python3") {
    $pythonCommand = "python3"
}

if (-not $pythonCommand) {
    throw "No se encontro Python. Instala Python 3.10+ y vuelve a ejecutar start.cmd."
}

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    cmd.exe /c "$pythonCommand -m venv .venv"
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

cmd.exe /c "`"$venvPython`" -m pip install -r requirements.txt"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

cmd.exe /c "`"$venvPython`" app.py"
exit $LASTEXITCODE
