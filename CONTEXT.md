# Contexto — PlaylistAI

Notas operativas del proyecto que no están en el README. Mantener actualizado con cada cambio relevante de seguridad o arquitectura.

## Qué es
Flask monolítico de un solo archivo (`app.py`) + frontend vanilla HTML/CSS/JS en `templates/index.html`. Curador de playlists de Spotify con IA (Anthropic/OpenAI/NVIDIA NIM). Uso personal, corre en `127.0.0.1:5000`.

## Catálogo de modelos de IA

Actualizado el 2026-07-16 contra la documentación oficial de OpenAI y Anthropic:

- **OpenAI**: GPT-5.6 Terra (`gpt-5.6-terra`, predeterminado/recomendado), GPT-5.6 Sol (`gpt-5.6-sol`, máxima calidad) y GPT-5.6 Luna (`gpt-5.6-luna`, rápido/económico).
- **Anthropic**: Claude Sonnet 5 (`claude-sonnet-5`, predeterminado/recomendado), Claude Opus 4.8 (`claude-opus-4-8`, máxima calidad) y Claude Haiku 4.5 (`claude-haiku-4-5-20251001`, rápido/barato).
- **NVIDIA NIM**: conserva su catálogo actual y DeepSeek V4 Pro como predeterminado.

Los modelos anteriores se retiraron del selector para mantener únicamente las familias vigentes. `updateModelSelect()` valida el modelo guardado en `localStorage`; si ya no existe para el proveedor seleccionado, lo reemplaza por el primer modelo vigente antes de enviar nuevas consultas.

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

### Respuestas de Anthropic por bloques
La Messages API no garantiza que `content[0]` sea texto: Claude puede anteponer bloques `thinking` u otros tipos al bloque `text`. `anthropic_response_text()` recorre toda la lista, ignora de forma explícita los bloques no textuales y concatena únicamente los `text`. No volver a leer la respuesta con `data["content"][0]["text"]`; además de causar `KeyError`, esa suposición puede intentar tratar razonamiento interno como respuesta visible.

Sonnet 5 activa adaptive thinking por defecto y los tokens de razonamiento cuentan dentro de `max_tokens`. Con el límite antiguo de 1500 llegó a devolver solo un bloque `thinking` con `stop_reason=max_tokens`, sin respuesta visible. `call_ai()` envía `thinking: {"type": "disabled"}` para todos los modelos Anthropic del selector y usa `max_tokens=4000`: estas rutas requieren JSON breve y determinista, no razonamiento extendido. Si no llega texto, el log registra solo tipos de bloque, `stop_reason` y cantidad de tokens; nunca el contenido del razonamiento.

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

### 2026-07-04 — Ronda de robustez (post-auditoría)
Comprobación general sin hallazgos de seguridad nuevos; se aplicaron 8 fixes de robustez:
1. `rejected` por fin se llena: `find_spotify_track()` acepta `filtered_out` y registra candidatos descartados por `track_allowed_by_prompt`; `resolve_items` clasifica la sugerencia como "rechazada por prompt" (en vez de "no encontrada") cuando Spotify sí la tenía pero el filtro la descartó. Antes la lista se declaraba y se mandaba al frontend pero nadie hacía append — la fila "Rechazada por prompt" del UI nunca aparecía.
2. Filtro del prompt por **palabra completa** (` term ` con padding de espacios sobre el label normalizado), no substring: antes `"rock"` rechazaba "Rocket Man" y `"ron"` rechazaba cualquier título con "electronic". Ojo: `normalize_search_text` ya elimina contenido entre paréntesis, así que "(feat. X)" nunca llega al filtro — solo se filtra "feat" fuera de paréntesis (comportamiento de siempre).
3. La respuesta JSON de la IA se trata como no confiable en `api_analyze`/`api_apply`: items sin `name`/`artist` o que no son dicts ya no tiran KeyError→500 (`.get()` + `isinstance`).
4. `refresh_token_if_needed()`: si el refresh falla (acceso revocado en Spotify), limpia la sesión y devuelve `False` → el frontend recibe 401 "reconecta" en vez de un 500.
5. `count` no numérico en `/api/create` y `/api/add_to_playlist` → 400 con mensaje claro (antes 500).
6. `except:` desnudo de `api_analyze` → `except Exception:`.
7. `SPOTIFY_SEARCH_CACHE`/`SPOTIFY_ARTIST_CACHE` con tope (`CACHE_MAX_ENTRIES = 500`): al llegar al tope se vacían (flush simple, no LRU — suficiente para proceso local).
8. `SESSION_COOKIE_SAMESITE = "Lax"` (Flask no pone SameSite por defecto) — capa extra anti-CSRF.

`requirements.txt` se queda con `>=` a propósito: con `==` no entran parches de seguridad solos y la app es local.

## Diseño / responsive

App pensada para desktop. Revisión visual 2026-07-02 identificó oportunidades de UI; todas resueltas a la fecha.

- **Breakpoint 1180px** (tablets landscape y laptops de 13"): sidebar 240px→208px, se quita `max-width:900px` del contenido, checkboxes de tracks 16px→20px (touch), más padding en botones.
- **Nav mobile <768px**: antes el sidebar se ocultaba por completo sin reemplazo (usuario quedaba atado a la página que cargó). Ahora hay `.mobile-topbar` (logo + avatar + "Salir", `position:sticky`) y `.mobile-tabbar` (4 botones, `position:fixed` abajo, mismo `data-page` que el sidebar). `showPage(name)` en el JS ya no depende del `event` global — usa `document.querySelectorAll('[data-page]')` para sincronizar el estado activo entre sidebar y tabbar a la vez. `.page` gana `padding-bottom` extra (`calc(84px + env(safe-area-inset-bottom))`) para no quedar tapada por el tabbar fijo.
- **Labels sin `for=`**: los 8 `<label class="input-label">` del formulario ahora tienen `for="<id-del-input>"` (playlist-url, create-name, create-mood, create-count, add-url, add-mood, add-count, model-select).
- **Contraste de `--text-muted`**: `#5a5a5a` (~3.4:1 sobre `--bg-base`, bajo WCAG AA) → `#7a7a7a` (~4.6:1). Cambio de una sola variable CSS, efecto global (track-num, section-count, pl-meta, empty state, logout-btn, placeholders).
- **Álbum art**: `app.py` ahora manda `playlist_image` (portada de la playlist, campo `images` agregado a `sp.playlist(..., fields=...)`) y `image` por track (`album.images`, tomando el más chico disponible con `smallest_image_url()`). En el frontend, `res-pl-cover` muestra la portada real si existe (si no, cae al ícono SVG de siempre) y cada fila de `track-item` tiene un thumbnail de 36×36 vía `trackThumbHtml()`.
  - **Limitación intencional**: las canciones en "Canciones a eliminar" (`ai.remove`) sí tienen portada real porque ya están matcheadas contra la playlist. Las sugerencias de "Canciones sugeridas" (`ai.add`) son propuestas de la IA aún no verificadas contra el catálogo de Spotify (eso pasa recién al aplicar, en `/api/apply` → `find_spotify_track`), así que muestran un ícono placeholder neutro en vez de buscar el track especulativamente solo para mostrar arte — evita llamadas extra a la API de Spotify y más latencia por una mejora puramente cosmética.
- **Crear con IA / Agregar a playlist** (2026-07-02, segunda vuelta): el stream NDJSON de `/api/create` y `/api/add_to_playlist` ahora manda `resolved_tracks` (antes solo mandaba `added`, puros strings) con `{id, label, image}` por cada canción ya resuelta contra Spotify — se arma en `stream_resolve_from_prompt()` en el mismo punto donde ya se llamaba `candidate_label(track)`. El frontend (`renderJobResultList`) usa `resolved_tracks` en vez de `added` para poder mostrar el thumbnail de 22×22 junto a cada resultado. Estas SÍ son canciones ya verificadas (a diferencia de las sugerencias de Analizar), así que no hay limitación de placeholder aquí.

- **Crédito de autor** (2026-07-04): "Creado por Kerwil Gil · © <año> Todos los derechos reservados" en tres lugares: `.sidebar-bottom` (desktop), `.credit-main` al final de `.main` (solo visible <768px, con el clearance del tabbar movido del `.page` al propio bloque) y bajo el botón de conectar en el login. El año se rellena por JS (`new Date().getFullYear()` sobre `.credit-year`) en un `<script>` compartido fuera del `{% if %}` para que corra también en el login; el "2026" hardcodeado en el HTML es solo fallback sin JS.

Verificado visualmente con iframes de ancho fijo (1280/1024/768/390px — el `resize_window` de la extensión de Chrome no afecta el viewport real en esta máquina, usar iframes si hace falta reverificar) y clicks reales dentro del iframe de 390px confirmando que el tabbar cambia de página y sincroniza el estado activo.

## Cómo levantar en local
```
uv run --with-requirements requirements.txt python app.py
```
o `start.ps1` / `start.cmd` / `start.sh` (detectan `uv`, si no existe crean `.venv`).
