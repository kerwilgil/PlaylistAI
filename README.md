# PlaylistAI

Curador inteligente de playlists de Spotify con IA.

## Setup local

1. Entra a <https://developer.spotify.com/dashboard>.
2. Crea o abre tu app de Spotify.
3. En `Settings -> Redirect URIs`, agrega exactamente:

```text
http://127.0.0.1:5000/callback
```

4. Crea un archivo `.env` basado en `.env.example`:

```text
SPOTIFY_CLIENT_ID=tu_client_id
SPOTIFY_CLIENT_SECRET=tu_client_secret
SPOTIFY_REDIRECT_URI=http://127.0.0.1:5000/callback

ANTHROPIC_API_KEY=tu_anthropic_key
OPENAI_API_KEY=
NVIDIA_API_KEY=
```

Solo necesitas una API key de IA para usar las funciones de analisis/creacion.

## Ejecutar

En Windows PowerShell:

```powershell
.\start.ps1
```

En bash:

```bash
bash start.sh
```

Abre <http://127.0.0.1:5000>.

## Funciones

- Analizar una playlist existente y sugerir canciones para quitar/agregar.
- Crear una playlist nueva desde un mood o concepto.

## Nota de OAuth

Si el boton de Spotify vuelve a la pantalla inicial con un aviso, revisa que `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` y el Redirect URI de Spotify coincidan exactamente con `http://127.0.0.1:5000/callback`.
