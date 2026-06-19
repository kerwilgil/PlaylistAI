$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "PlaylistAI corriendo en http://127.0.0.1:5000"
Write-Host ""

uv run --with flask --with spotipy --with requests python app.py
