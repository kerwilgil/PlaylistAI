# Contexto — PlaylistAI

Notas operativas del proyecto que no están en el README. Mantener actualizado con cada cambio relevante de seguridad o arquitectura.

## Qué es
Flask monolítico (`app.py`) + frontend vanilla HTML/CSS/JS en `templates/index.html`. Curador de playlists de Spotify con IA mediante API (Anthropic/OpenAI/NVIDIA NIM) o suscripción local (Claude Code/Codex). Uso personal, corre en `127.0.0.1:5000`.

## Suscripciones locales de IA (1.0.0)

`local_ai.py` integra Claude Code y Codex como procesos no interactivos. La
pantalla de Configuración separa primero el modo `subscription`/`api`, después
filtra los proveedores y modelos. La preferencia se guarda en `localStorage`
como `ai_access`, `ai_provider` y `ai_model`.

- `/api/providers` comprueba instalación y autenticación con
  `claude auth status` o `codex login status` sin devolver identidad, correo u
  otros datos de cuenta al navegador.
- Las variables `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `OPENAI_API_KEY` y
  `CODEX_API_KEY` se eliminan del entorno hijo cuando se usa una suscripción.
- Claude Code se ejecuta con salida JSON, sin herramientas, sin comandos slash,
  sin persistencia de sesión y en modo seguro/no interactivo.
- Codex se ejecuta de forma efímera, sin reglas del repositorio, en sandbox de
  solo lectura y dentro de un directorio temporal vacío.
- La detección amplía `PATH` con ubicaciones habituales de npm, Homebrew, nvm,
  fnm, asdf y `~/.local/bin`; es necesaria para una `.app` iniciada desde Finder.
- Los procesos tienen timeout controlado y sus errores se convierten en mensajes
  de aplicación. Nunca se registra el prompt ni la respuesta completa.

La suscripción solo reemplaza la credencial del proveedor de IA; Spotify todavía
requiere `SPOTIFY_CLIENT_ID` y `SPOTIFY_CLIENT_SECRET` en `.env`.

## Builds de escritorio

Desde 2026-07-23 hay builds reproducibles con PyInstaller 6.21:

- `desktop.py` levanta el mismo Flask únicamente en `127.0.0.1:5000`, abre el
  navegador y permanece silencioso en segundo plano, sin consola ni ventana
  flotante. Una segunda ejecución detecta el servidor existente, abre la página
  y termina sin crear otra instancia.
- `scripts/build_windows.ps1` genera un binario de un solo archivo en
  `dist/windows/PlaylistAI.exe`.
- `scripts/build_macos.sh` genera `dist/macos/PlaylistAI.app`, aplica firma ad
  hoc para uso local, la verifica con `codesign` y crea
  `dist/macos/PlaylistAI-macOS.zip`.
- `assets/playlistai-icon.ico` y `assets/playlistai-icon.icns` contienen el icono
  nativo de cada plataforma; `assets/playlistai-icon.png` es la fuente maestra
  transparente de 1024×1024.
- PyInstaller no es cross-compiler: el `.exe` se construye en Windows y el
  `.app` en macOS. El build macOS es nativo para la arquitectura del Python/Mac
  usado para compilar.

Los scripts eliminan `build/` después de validar el artefacto final. El build de
Windows también retira ejecutables con nombres antiguos para no conservar
duplicados ni confundirlos con la versión vigente.

Las credenciales nunca se empaquetan. `.env.example` sí viaja como recurso y el
primer arranque crea una copia escribible: junto a `PlaylistAI.exe` en Windows y
en `~/Library/Application Support/PlaylistAI/.env` en macOS. Los logs del
lanzador se guardan en el mismo directorio de configuración. Las variables
`PLAYLISTAI_CONFIG_DIR` y `PLAYLISTAI_ENV_FILE` permiten sobrescribir estas rutas;
`PLAYLISTAI_NO_BROWSER=1` desactiva las aperturas automáticas para pruebas.

La firma ad hoc de macOS solo cubre uso local. Distribuir a otros Macs sin avisos
de Gatekeeper exige certificado Developer ID y notarización de Apple.

## Catálogo de modelos de IA

Actualizado el 2026-07-16 contra la documentación oficial de OpenAI y Anthropic:

- **OpenAI**: GPT-5.6 Terra (`gpt-5.6-terra`, predeterminado/recomendado), GPT-5.6 Sol (`gpt-5.6-sol`, máxima calidad) y GPT-5.6 Luna (`gpt-5.6-luna`, rápido/económico).
- **Anthropic**: Claude Sonnet 5 (`claude-sonnet-5`, predeterminado/recomendado), Claude Opus 4.8 (`claude-opus-4-8`, máxima calidad) y Claude Haiku 4.5 (`claude-haiku-4-5-20251001`, rápido/barato).
- **NVIDIA NIM**: conserva su catálogo actual y DeepSeek V4 Pro como predeterminado.

Los modelos anteriores se retiraron del selector para mantener únicamente las familias vigentes. `updateModelSelect()` valida el modelo guardado en `localStorage`; si ya no existe para el proveedor seleccionado, lo reemplaza por el primer modelo vigente antes de enviar nuevas consultas.

## Resolución incremental por rondas (1.1.0)

Antes (hasta 1.0.0): `/api/create` y `/api/add_to_playlist` pedían `count + 6`
candidatos en UNA sola llamada gigante a la IA, con un único retry fijo y un
"loop de reparación" cuyo tope de rondas estaba invertido (`1 if count > 30
else 2` — pedidos grandes tenían MENOS margen para completarse). Con
`count=50` eso era una sola llamada pidiendo ~55 canciones de golpe: lenta,
frágil (más chance de JSON truncado) y con solo ~12% de oversampling. Root
cause del bug "pido 50, llegan 10".

Ahora `stream_resolve_from_prompt()` es un único loop de rondas incremental,
sin distinguir "creación inicial" vs "reparación" — cada ronda pide un lote
chico, se resuelve contra Spotify, y el loop sigue hasta completar el target o
cortar por una condición explícita:

- **Oversampling dinámico** (`_round_batch_size`): `ceil(needed * 1.4)`, con
  piso de 6 (que valga la pena la llamada a IA) y techo de 18 (`BATCH_MIN_SIZE`
  / `BATCH_MAX_SIZE`) por ronda. Se recalcula sobre `needed` en cada ronda, así
  que nunca crece sin control.
- **Rondas adaptativas** (`_max_rounds_for_count`): `max(4, min(12,
  ceil(count/5) + 2))` — pedidos grandes permiten más rondas, con techo fijo
  de 12 para nunca loopear indefinidamente.
- **Corte por falta de progreso**: `NO_PROGRESS_ROUND_LIMIT = 2` — dos rondas
  seguidas sin sumar ninguna canción cortan el loop (`stop_reason="no_progress"`).
- **Deadline por ronda, no solo global**: antes de arrancar una ronda (excepto
  la ronda 0, que siempre se intenta al menos una vez para no devolver un
  resultado vacío) se compara el tiempo restante contra
  `_round_time_budget_seconds(provider)` — el timeout real de la llamada a IA
  (`LOCAL_AI_TIMEOUT_SECONDS` para Claude Code/Codex, 60s de `requests` para
  las APIs) + margen. Si no alcanza razonablemente, corta con
  `stop_reason="deadline"` en vez de arrancar una ronda que probablemente no
  terminaría a tiempo.
- **Historial de intentos** (`attempted_normalized`, clave `"artist|name"`
  normalizada con `normalize_search_text`): acumula TODO lo intentado en el
  job — aceptado, rechazado por prompt, no encontrado, y duplicados de
  Spotify ID — para (a) filtrar candidatos repetidos de la IA ANTES de gastar
  una búsqueda de Spotify y (b) prohibirle explícitamente a la IA repetirlos
  en el prompt de la siguiente ronda.
- **Prompt unificado** (`_round_prompt`): reemplaza a los antiguos
  `_create_prompt`/`_retry_prompt`/`_repair_prompt`. La ronda 0 pide
  descripción + candidatos iniciales; las siguientes reciben la lista de
  aceptadas y de intentadas (capada a las últimas 60 para no inflar el
  prompt) con prohibición explícita de repetirlas.
- **`result["stop_reason"]`**: nuevo campo interno con el motivo exacto de
  parada (`"completed"`, `"max_rounds"`, `"no_progress"`, `"deadline"` o
  ausente si hubo `fatal`). Se expone también en el evento `done` del NDJSON
  (campo `stop_reason`) sin romper los campos que el frontend ya leía
  (`added`, `resolved_tracks`, `not_found`, `rejected`, `timed_out`, etc.).
  `timed_out` ahora se calcula como `stop_reason == "deadline"` (además del
  chequeo de reloj que ya existía), porque el corte por deadline sucede CON
  margen de seguridad antes del `deadline` real — el chequeo de reloj solo no
  bastaría para detectarlo en todos los casos.

`PLAYLISTAI_JOB_TIMEOUT_SECONDS` (nueva env var, default 360s / 6 min, clamp
120–600s, ver `.env.example` y `_job_timeout_seconds()` en `app.py`)
reemplaza el `deadline = time.monotonic() + 220` hardcodeado. El mismo valor
(+ `JOB_TIMEOUT_FRONTEND_MARGIN_SECONDS = 30`, en milisegundos) se manda al
template en `index()` como `job_timeout_ms` y se usa en
`templates/index.html` (`const JOB_TIMEOUT_MS`) para el `AbortController` de
`streamPlaylistJob()` — ya no hay un `270000` hardcodeado desacoplado del
backend; ambos números viven en un solo lugar de verdad (`app.py`).

**Relación con `LOCAL_AI_TIMEOUT_SECONDS`** (`local_ai.py`): antes cubría una
única llamada pidiendo hasta ~55 candidatos; con lotes de máximo 18 por ronda
se bajó de 180s a 100s — sigue siendo holgado para una respuesta más chica y
deja más margen dentro del mismo `PLAYLISTAI_JOB_TIMEOUT_SECONDS` para que
quepan más rondas cuando se usa una suscripción local (Claude Code/Codex).

El `candidate_count` que antes se pasaba como parámetro externo a
`stream_resolve_from_prompt()` desapareció: el tamaño de cada lote se calcula
internamente por ronda vía `_round_batch_size(needed)`, así que la firma de la
función cambió (ya no recibe `candidate_count`) — ver `tests/test_ai_responses.py`
y el nuevo `tests/test_incremental_rounds.py` para la firma vigente.

## Restricciones duras vs preferencias suaves (1.1.0)

Bug real (auditado): un usuario pidió "Lo-Fi / Chillhop instrumental... sin
voces..." y la app agregó canciones CON voces. Causa raíz: la función que
activaba el filtro de instrumental (`detect_hard_constraints`, antes
`prompt_needs_instrumental_electronic_filter`) exigía DOS cosas a la vez —
mención de instrumental/sin-voces Y contexto de género electrónico
(techno/house/deep/...). "Lo-Fi"/"Chillhop" no calificaban como género
electrónico, así que el filtro nunca se activaba y `track_allowed_by_prompt`
dejaba pasar cualquier candidato, tuviera voz o no.

**Decisión de diseño**: las restricciones explícitas del usuario tienen
prioridad sobre alcanzar `target_count`. Es preferible devolver una playlist
incompleta (menos canciones de las pedidas) que una completa que viole una
restricción dura. Esto ya lo garantiza el loop de rondas existente
(`stream_resolve_from_prompt` → `no_progress`/`max_rounds`/`deadline` como
`stop_reason`) una vez que el filtro se aplica correctamente — el bug NO
estaba en el loop de rondas, estaba en que el filtro casi nunca se activaba.

**Restricciones duras** (`detect_hard_constraints(mood)`, calculada UNA VEZ al
inicio del job a partir del mood original — el mood no cambia entre rondas —
y reforzada literalmente en el prompt de CADA ronda vía `_round_prompt`, no
solo en la ronda 0):
- `instrumental`: "instrumental", "sin voces", "sin voz", "no vocal",
  "without vocals". Activada SOLO por sus propios términos — YA NO exige
  contexto de género electrónico.
- `no_remix`: "sin remix(es)", "no remix(es)".
- `no_live`: "sin/no live", "sin/no en vivo".

Todas se verifican en TODAS las rondas y en el fallback de artista
(`find_artist_fallback`), nunca solo en la búsqueda exacta — ambas rutas
pasan por `track_allowed_by_prompt(sp, track, mood)`, que internamente llama
a `detect_hard_constraints(mood)` (recómputo puro y barato sobre el mismo
mood invariable del job — comportamiento idéntico a pasar el dict ya
calculado, sin forzar un parámetro nuevo en `find_spotify_track`/
`find_artist_fallback`, cuya firma se mantuvo estable a propósito para no
romper los mocks de `tests/test_incremental_rounds.py`).

**Preferencias suaves** (NO activan ningún filtro de rechazo — solo refuerzan
tono en el prompt a la IA, ver `_round_prompt`): "programar", "enfoque",
"concentración", "focus", "trabajo", "estudiar", "relajante", "deep",
"smooth", etc. Antes vivían mezcladas con la detección de "instrumental"
(cualquier mood de "trabajo" activaba sin querer el filtro de voces si además
mencionaba género electrónico, o nunca lo activaba si no lo mencionaba) — con
el fix quedaron separadas: nunca convierten un mood en prohibición absoluta
de voces por sí solas.

`electronic_context` (techno/house/electronic/deep/melodic/minimal/
progressive/microhouse) se conserva, pero cambió su rol: ya NO decide si se
activa la restricción dura de instrumental. Solo decide si
`track_allowed_by_prompt` aplica vocabulario ADICIONAL de rechazo específico
del caso original (acoustic/unplugged/radio edit + géneros no-electrónicos
como salsa/reggaeton/rock/pop/etc.) — para no sobre-filtrar esos géneros en
moods sin ese contexto (ej. un Lo-Fi instrumental sí puede incluir samples con
textura "acoustic" en el título sin que eso implique voz).

**Limitación real conocida**: la Search API de Spotify NO expone ningún
atributo confiable de "instrumental" vs "vocal" (no hay audio-features de
ese tipo disponibles aquí, y no se agregaron llamadas nuevas a la API para
esto). La detección de voz es una heurística sobre el TEXTO del
título/álbum del candidato (`candidate_label` — términos como "feat",
"vocal", "vocal mix", "with vocals"), no una garantía. Un track con voz cuyo
título no lo delata puede colarse; esto es una limitación de plataforma, no
un bug de esta app.

## Parche de compatibilidad: Search API limit=10 en Development Mode (1.1.0)

Detectado en verificación en vivo (2026-08-18): la Search API de Spotify
(`GET /search`) para esta app, registrada en **Development Mode**, ahora
rechaza con `400 Invalid limit` cualquier `limit` mayor a **10** — antes
aceptaba hasta 20-50. Confirmado directamente contra la API real (fuera de
sesión de usuario, solo con `SPOTIFY_CLIENT_ID`/`SPOTIFY_CLIENT_SECRET` vía
client_credentials): `limit=10` → 200, `limit=15`/`20`/`50` → 400.

Esto rompía en silencio `find_artist_fallback()` (pedía `limit=20`): cada
intento de fallback lanzaba una `SpotifyException` que `resolve_one` ya
capturaba como "no encontrada" — sin crashear, pero sin usar nunca el
fallback real de artista, y sumando llamadas fallidas que aceleran el 429.

Fix: `spotify_search()` clampea a `min(limit, 10)` (antes `20`) como última
línea de defensa, y `find_artist_fallback()` pide explícitamente `limit=10`
para reflejar el contrato real. `find_spotify_track()` ya pedía `limit=5`,
sin cambios. No se tocaron límites de otros endpoints (p.ej. `limit=100` de
`sp.playlist_items()`, que es un endpoint distinto con su propio máximo).

Si Spotify vuelve a ajustar esta cuota (subirla o bajarla más), el punto
único de verdad es el clamp dentro de `spotify_search()` — no hay que
perseguir cada `limit=` del archivo.

### Diferenciar `QUOTA_EXCEEDED` de un 429 genérico

`SpotifyException` (spotipy) expone `.reason`, tomado del campo `reason` del
cuerpo de error de Spotify cuando lo incluye. El manejo de 429 en
`stream_resolve_from_prompt` ahora distingue: si `e.reason == "QUOTA_EXCEEDED"`
el mensaje le dice al usuario que es un límite de cuota de la app (no un rate
limit pasajero) y a revisar el Developer Dashboard; cualquier otro 429 sigue
mostrando el mensaje genérico de "espera unos minutos". No siempre viene ese
`reason` — depende de si Spotify lo incluye en el cuerpo de esa respuesta en
particular — así que esto es un mejor-esfuerzo, no una garantía.

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

Sonnet 5 activa adaptive thinking por defecto y los tokens de razonamiento cuentan dentro de `max_tokens`. Con el límite antiguo de 1500 llegó a devolver solo un bloque `thinking` con `stop_reason=max_tokens`, sin respuesta visible. `call_ai()` envía `thinking: {"type": "disabled"}` para todos los modelos Anthropic del selector. La creación de playlists calcula un presupuesto de 6.000–12.000 tokens según el número de candidatos y puede subir hasta 16.000 en un único reintento automático. Además, Anthropic recibe `output_config.format` con JSON Schema para garantizar la forma `{description, tracks}` (o `{tracks}` en reemplazos). `parse_ai_json()` tolera fences y texto periférico; `parse_playlist_json()` valida nombre/artista y rechaza salidas vacías o truncadas antes de buscar en Spotify. Si el primer resultado es inválido se reintenta una vez; si no llega texto, el log registra solo tipos de bloque, `stop_reason` y cantidad de tokens, nunca el contenido del razonamiento.

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

- **Tema de interfaz** (2026-07-23): tres preferencias persistidas en
  `localStorage` bajo `playlistai_theme`: `system` (predeterminada), `dark` y
  `light`. El atributo `data-theme` se aplica en `<html>` antes de cargar el CSS
  para evitar parpadeos. `system` usa `prefers-color-scheme` y reacciona a
  cambios del sistema operativo sin recargar. Hay acceso rápido en login/sidebar
  y un selector con etiquetas en Configuración; todos los controles se
  sincronizan con `aria-pressed`.
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
