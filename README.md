# PlaylistAI

Curador inteligente de playlists de Spotify con IA. Describe un mood o concepto y
la app genera la playlist directo en tu cuenta de Spotify, verificando cada canción
contra el catálogo real antes de crearla.

## Funciones

- **Crear con IA**: describe el mood/concepto y la cantidad de canciones, y la app
  genera la playlist en tu Spotify con **progreso en vivo** ("Verificando 1/30…").
- **Analizar playlist**: pega el link de una playlist y la IA sugiere qué quitar y
  qué agregar.
- **Multi-proveedor de IA**: Anthropic (Claude), OpenAI (GPT) y NVIDIA NIM,
  seleccionables desde *IA Config*.

## Cómo funciona (creación)

1. La IA propone una lista de canciones para tu concepto.
2. Cada sugerencia se **busca en Spotify en paralelo** y se valida que el
   **artista coincida de verdad** (no se aceptan canciones del título correcto pero
   de otro artista).
3. Si la IA inventó un título que no existe, se usa una canción **real y popular del
   mismo artista** como respaldo (se marca como *"sustituto (mismo artista)"*).
4. La playlist se crea en Spotify solo cuando las canciones están verificadas.

## Setup local

1. Entra a <https://developer.spotify.com/dashboard> y crea una app.
2. En `Settings -> Redirect URIs`, agrega **exactamente**:

   ```text
   http://127.0.0.1:5000/callback
   ```

   No uses `localhost` ni `oauth.pstmn.io`.
3. **Importante (Development Mode):** una app nueva solo permite cuentas que
   agregues a mano. Ve a `Settings -> User Management` y agrega tu correo/usuario de
   Spotify, o el login dará error.
4. Revisa el archivo `.env` basado en `.env.example`:

   ```text
   SPOTIFY_CLIENT_ID=tu_client_id
   SPOTIFY_CLIENT_SECRET=tu_client_secret
   SPOTIFY_REDIRECT_URI=http://127.0.0.1:5000/callback

   ANTHROPIC_API_KEY=tu_anthropic_key
   OPENAI_API_KEY=
   NVIDIA_API_KEY=
   ```

   Si `.env` no existe, los scripts de arranque lo crean automaticamente desde
   `.env.example`. Basta **una** API key de IA para usar las funciones de
   análisis/creación.

## Ejecutar

En Windows PowerShell:

```powershell
.\start.ps1
```

En Windows por doble clic:

```text
start.cmd
```

En macOS por doble clic:

```text
start.command
```

En bash:

```bash
bash start.sh
```

Luego abre <http://127.0.0.1:5000> y conecta tu cuenta de Spotify.

> Si [`uv`](https://docs.astral.sh/uv/) esta instalado, el arranque lo usa para
> resolver dependencias automaticamente. Si no, crea un entorno local `.venv`,
> instala `requirements.txt` con `pip` y corre `python app.py`.

## Modelos por defecto

Por calidad, cada proveedor arranca en su mejor modelo (Claude Opus 4.8 /
GPT-5.4 / DeepSeek V4 Pro). En *IA Config* puedes cambiar a uno más rápido
(p. ej. Claude Sonnet 4.6) si prefieres velocidad.

## Solución de problemas

- **Vuelve a la pantalla inicial con un aviso al conectar**: revisa que
  `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` y el Redirect URI coincidan
  exactamente con `http://127.0.0.1:5000/callback`, y que tu cuenta esté en
  *User Management*.
- **"Spotify limitó las peticiones (429)"**: hiciste demasiadas peticiones en poco
  tiempo y Spotify bloqueó la app (el bloqueo es por `client_id` y puede durar
  horas). Espera, o crea otra app con credenciales nuevas. La app ya está
  optimizada (1 búsqueda por canción, en paralelo) para no provocarlo en uso normal.
- **403 al crear/editar**: cierra sesión con *Salir*, reconecta y acepta los
  permisos de modificación de playlists.

## Seguridad

El archivo `.env` (claves de Spotify y de IA) está en `.gitignore` y **no se sube**
al repositorio. Cada quien usa sus propias credenciales.
