<p align="center">
  <img src="assets/playlistai-icon.png" alt="Icono de PlaylistAI: lista musical con una onda y un destello de IA" width="180">
</p>

<h1 align="center">PlaylistAI</h1>

<p align="center">
  Curador inteligente de playlists de Spotify con IA.<br>
  Describe una idea y recibe una playlist verificada contra el catálogo real de Spotify.
</p>

<p align="center">
  <a href="https://github.com/kerwilgil/PlaylistAI/releases/tag/v0.1.0"><strong>Descargar PlaylistAI 0.1.0</strong></a>
</p>

## Descargar

- **Windows 10/11 (x64):** descarga
  [`PlaylistAI-0.1.0-Windows-x64.zip`](https://github.com/kerwilgil/PlaylistAI/releases/download/v0.1.0/PlaylistAI-0.1.0-Windows-x64.zip),
  extrae la carpeta y abre `PlaylistAI.exe`.
- **macOS:** descarga el código fuente desde la
  [release 0.1.0](https://github.com/kerwilgil/PlaylistAI/releases/tag/v0.1.0)
  y genera `PlaylistAI.app` en el propio Mac con `bash scripts/build_macos.sh`.

La aplicación funciona localmente en `127.0.0.1:5000`. Tus credenciales permanecen
en el archivo `.env` de tu equipo y no se incorporan al ejecutable.

## Funciones

- **Crear con IA**: describe el mood/concepto y la cantidad de canciones, y la app
  genera la playlist en tu Spotify con **progreso en vivo** ("Verificando 1/30…").
- **Analizar playlist**: pega el link de una playlist y la IA sugiere qué quitar y
  qué agregar.
- **Multi-proveedor de IA**: Anthropic (Claude), OpenAI (GPT) y NVIDIA NIM,
  seleccionables desde *IA Config*.
- **Tema adaptable**: Sistema (predeterminado), Oscuro o Claro. La preferencia se
  guarda en el navegador; el modo Sistema sigue automáticamente la apariencia
  configurada en Windows o macOS.

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

   Si `.env` no existe, los scripts de arranque lo crean automáticamente desde
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

> Si [`uv`](https://docs.astral.sh/uv/) está instalado, el arranque lo usa para
> resolver dependencias automáticamente. Si no, crea un entorno local `.venv`,
> instala `requirements.txt` con `pip` y corre `python app.py`.

## Aplicación de escritorio

Los builds de escritorio siguen siendo completamente locales: levantan PlaylistAI
solo en `127.0.0.1:5000` y abren la interfaz en tu navegador. Se ejecutan
silenciosamente en segundo plano, sin consola ni ventana flotante. Cerrar el
navegador no detiene el servidor; volver a ejecutar la aplicación reabre la
página sin iniciar otra copia.
El icono propio de PlaylistAI combina una lista, una onda musical y un destello de
IA; se incluye en los builds de Windows y macOS.

### Crear el `.exe` de Windows

Desde PowerShell:

```powershell
.\scripts\build_windows.ps1
```

El resultado queda, desde la raíz del repositorio, en:

```text
.\dist\windows\PlaylistAI.exe
```

Para mostrar su ruta completa en PowerShell:

```powershell
(Resolve-Path .\dist\windows\PlaylistAI.exe).Path
```

El primer arranque crea `dist\windows\.env` desde la plantilla y lo abre para que
añadas tus credenciales; guarda el archivo y vuelve a ejecutar
`PlaylistAI.exe`. El
archivo `.env` nunca se incorpora al ejecutable. Para detener completamente el
servidor en Windows, finaliza `PlaylistAI.exe` desde el Administrador de tareas.
La carpeta `dist\` está excluida de Git. Puedes descargar el binario desde la
[release 0.1.0](https://github.com/kerwilgil/PlaylistAI/releases/tag/v0.1.0) o
generarlo en Windows ejecutando el script de build.

### Crear el `.app` de macOS

PyInstaller no hace compilación cruzada: este paso debe ejecutarse en el propio
Mac donde se usará la aplicación.

```bash
bash scripts/build_macos.sh
```

Los resultados quedan en `dist/macos/PlaylistAI.app` y
`dist/macos/PlaylistAI-macOS.zip`. La configuración se guarda en
`~/Library/Application Support/PlaylistAI/.env`, fuera del bundle. El script
aplica una firma ad hoc adecuada para uso local; distribuir el `.app` a otros
equipos requeriría una identidad Developer ID y notarización de Apple.

## Modelos por defecto

Cada proveedor arranca con una opción equilibrada y recomendada: Claude Sonnet 5,
GPT-5.6 Terra o DeepSeek V4 Pro. En *IA Config* puedes elegir máxima calidad
(Claude Opus 4.8 / GPT-5.6 Sol) o menor costo y latencia
(Claude Haiku 4.5 / GPT-5.6 Luna).

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

Para gotchas conocidos del código (ej. el manejo del cache de OAuth de spotipy),
historial de auditorías de seguridad y decisiones de diseño, ver [`CONTEXT.md`](CONTEXT.md).

## Responsive

Pensada para uso en desktop, pero funciona en cualquier tamaño:

- **Tablets y laptops de 13"** (≤1180px): sidebar más angosto y targets táctiles
  más grandes.
- **Celular** (≤768px): el sidebar se reemplaza por una barra superior (logo +
  cuenta) y una barra de navegación inferior fija con los 4 accesos principales.

## Accesibilidad

Labels de formulario enlazados a su input (`for=`/`id`), y el texto secundario
(`--text-muted`) cumple contraste WCAG AA (~4.6:1) sobre el fondo oscuro.
