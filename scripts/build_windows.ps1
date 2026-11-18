$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "No se encontró uv. Instálalo desde https://docs.astral.sh/uv/ y vuelve a ejecutar."
}

$BuildEnv = Join-Path ([System.IO.Path]::GetTempPath()) ("playlistai-build-" + [guid]::NewGuid())
try {
    uv venv --python 3.12.14 $BuildEnv
    if ($LASTEXITCODE -ne 0) { throw "No se pudo crear el entorno de build." }
    $BuildPython = Join-Path $BuildEnv "Scripts\python.exe"
    uv pip install --python $BuildPython --require-hashes --only-binary :all: -r requirements-build.lock
    if ($LASTEXITCODE -ne 0) { throw "No se pudieron instalar las dependencias fijadas." }
    & $BuildPython -m PyInstaller --noconfirm --clean --distpath dist/windows packaging/windows.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller falló con código de salida $LASTEXITCODE." }
} finally {
    if (Test-Path -LiteralPath $BuildEnv) { Remove-Item -LiteralPath $BuildEnv -Recurse -Force }
}

$OutputDirectory = Join-Path $ProjectRoot "dist\windows"
$OutputPath = Join-Path $OutputDirectory "PlaylistAI.exe"
if (-not (Test-Path -LiteralPath $OutputPath)) {
    throw "La compilación terminó sin crear dist/windows/PlaylistAI.exe."
}

Copy-Item -LiteralPath ".env.example" `
    -Destination (Join-Path $OutputDirectory ".env.example") `
    -Force

$LegacyOutputs = @(
    (Join-Path $ProjectRoot "dist\PlaylistAI.exe"),
    (Join-Path $ProjectRoot "dist\PlaylistAI-Desktop.exe"),
    (Join-Path $ProjectRoot "dist\PlaylistAI-Icono-Nuevo.exe"),
    (Join-Path $ProjectRoot "dist\.env.example")
)
foreach ($LegacyOutput in $LegacyOutputs) {
    if (Test-Path -LiteralPath $LegacyOutput) {
        Remove-Item -LiteralPath $LegacyOutput -Force
    }
}

$BuildDirectory = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "build"))
$ProjectPrefix = $ProjectRoot.TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar
) + [System.IO.Path]::DirectorySeparatorChar
if (-not $BuildDirectory.StartsWith(
    $ProjectPrefix,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "La ruta temporal calculada quedó fuera del repositorio: $BuildDirectory"
}
if (Test-Path -LiteralPath $BuildDirectory) {
    Remove-Item -LiteralPath $BuildDirectory -Recurse -Force
}

Write-Host ""
Write-Host "Build listo:"
Write-Host "  $OutputPath"
Write-Host ""
Write-Host "El primer arranque creará dist\windows\.env para tus credenciales."
