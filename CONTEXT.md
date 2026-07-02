# Contexto — PlaylistAI

Notas operativas del proyecto que no están en el README. Mantener actualizado con cada cambio relevante de seguridad o arquitectura.

## Qué es
Flask monolítico de un solo archivo (`app.py`) + frontend vanilla HTML/CSS/JS en `templates/index.html`. Curador de playlists de Spotify con IA (Anthropic/OpenAI/NVIDIA NIM). Uso personal, corre en `127.0.0.1:5000`.

## Gotchas conocidos del código

### `SpotifyOAuth(cache_handler=...)` — `None` NO desactiva el cache en disco
`spotipy` trata `cache_handler=None` como "no especificado" y cae a su `CacheFileHandler` por defecto, que escribe el token OAuth **en texto plano** a un archivo `.cache` en la raíz del repo, en cada login. No lo detecta con solo leer la firma del método — hay que revisar el comportamiento default de la librería.

Fix correcto: `cache_handler=MemoryCacheHandler()` (de `spotipy.cache_handler`). El token real de la app vive en `session["token_info"]` (Flask session, ver `callback()` y `get_sp()` en `app.py`), así que el cache de spotipy nunca hace falta en disco — un cache en memoria descartable por request es el equivalente correcto a "sin cache".

`.cache` está en `.gitignore` desde el primer commit, así que nunca se subió a GitHub, pero sí quedaba expuesto en el filesystem local con un `refresh_token` que no expira por tiempo.

### `app.secret_key = os.urandom(24)`
Se regenera en cada arranque del proceso → todas las sesiones activas (cookie de Flask) se invalidan al reiniciar el server, forzando reconectar Spotify. Es intencional (buena entropía, no hay persistencia de la key en disco) pero ten esto presente: **reiniciar el server durante una sesión activa desloguea al usuario**. Si algún día se corre con múltiples workers (gunicorn, etc.), cada uno tendría una key distinta y rompería sesiones de forma intermitente — mover la key a `.env` si se escala más allá de un proceso local único.

### Manejo de errores
Dos error handlers separados:
- `@app.errorhandler(HTTPException)` — en `/api/*` devuelve JSON con `error.description`; fuera de `/api/*` devuelve la respuesta HTTP original (preserva el código correcto: 404, 405, etc.).
- `@app.errorhandler(Exception)` — loguea completo server-side (`app.logger.exception`), devuelve mensaje genérico en `/api/*` y `InternalServerError()` genérico fuera de `/api/*`.

No usar `raise error` dentro de un errorhandler para "delegar" al manejo default de Flask — un re-raise ahí no re-despacha correctamente y convierte cualquier HTTPException (incluido 404) en un 500. Si se necesita delegar, `return error` (las instancias de `HTTPException` son respuestas WSGI válidas).

### Headers de seguridad
`@app.after_request` agrega `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Content-Security-Policy: frame-ancestors 'none'`, `Referrer-Policy: same-origin` a toda respuesta. Relevante sobre todo si algún día se expone la app fuera de `127.0.0.1`.

## Historial de auditorías de seguridad

### 2026-07-02 — Auditoría completa (MARTE, `auditoria-seguridad-sistema`)
Reporte completo: `.marte-audits/playlistai-security-2026-07-02/REPORTE.md` (en el repo de MARTE, no en este repo).

Hallazgos y fixes:
1. **[High] Token OAuth en texto plano en disco** — causa raíz: el gotcha de `cache_handler=None` de arriba. Fix: `MemoryCacheHandler()`. Se revocó el acceso viejo de la app en https://www.spotify.com/account/apps/ y se verificó en vivo (dos rondas — la primera corrección no fue suficiente) que `.cache` ya no se regenera tras reconectar.
2. **[Low] Error handler filtraba `str(error)` interno al cliente en `/api/*`** — fix: mensajes genéricos + logging server-side completo. De paso se corrigió el bug del `raise error` (ver arriba).
3. **[Low] Sin headers de seguridad HTTP** — fix: `after_request` con headers básicos.
4. `requirements.txt` sin versiones fijas — pinneado a `flask>=3.1.3`, `spotipy>=2.26.0`, `requests>=2.34.2`.

Lo que ya estaba bien (verificado, no solo revisado): CSRF de OAuth con `state` correcto, todas las rutas `/api/*` exigen sesión, cero XSS en frontend (`escapeHtml()` consistente + auto-escape de Jinja), CORS no habilitado bloquea CSRF cross-origin de facto, nunca se commiteó un secreto, `debug=False`.

## Diseño / responsive

App pensada para desktop. Revisión visual 2026-07-02 identificó, además del tema de seguridad, oportunidades de UI (no todas priorizadas — uso es local, un solo usuario):
- **Hecho**: breakpoint `@media (max-width: 1180px)` en `templates/index.html` para tablets landscape y laptops de 13" — sidebar 240px→208px, se quita `max-width:900px` del contenido, checkboxes de tracks 16px→20px (touch), más padding en botones. Verificado con iframes de ancho fijo a 1280/1024/768px (el `resize_window` de la extensión de Chrome no afectaba el viewport real en esta máquina — si hace falta reverificar, usar iframes en vez de resize).
- **Pendiente, no priorizado** (uso local, no crítico): sidebar se oculta por completo <768px sin navegación alternativa (usuario queda atado a la página que cargó); labels de formularios sin `for=`/`id` (accesibilidad); contraste de `--text-muted` (~3.4:1) bajo WCAG AA en texto secundario; sin portadas/álbum art reales de Spotify en las listas de tracks (mejora de "delight", no funcional).

## Cómo levantar en local
```
uv run --with-requirements requirements.txt python app.py
```
o `start.ps1` / `start.cmd` / `start.sh` (detectan `uv`, si no existe crean `.venv`).
