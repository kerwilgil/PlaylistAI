$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "No se encontró uv. Instálalo desde https://docs.astral.sh/uv/ y vuelve a ejecutar."
}

uv run `
    --with-requirements requirements.txt `
    --with-requirements requirements-build.txt `
    pyinstaller `
    --noconfirm `
    --clean `
    packaging/windows.spec

if (-not (Test-Path -LiteralPath "dist/PlaylistAI.exe")) {
    throw "La compilación terminó sin crear dist/PlaylistAI.exe."
}

Copy-Item -LiteralPath ".env.example" -Destination "dist/.env.example" -Force

Write-Host ""
Write-Host "Build listo:"
Write-Host "  $ProjectRoot\dist\PlaylistAI.exe"
Write-Host ""
Write-Host "El primer arranque creará dist\.env para que añadas tus credenciales."
