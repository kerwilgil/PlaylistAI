import math, os, re, json, secrets, time, unicodedata, requests
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode, urlparse
from flask import Flask, redirect, request, session, jsonify, render_template, Response, stream_with_context
from werkzeug.exceptions import HTTPException, InternalServerError
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException
from spotipy.cache_handler import MemoryCacheHandler
import spotipy
from runtime_paths import env_file_path
from local_ai import LOCAL_AI_TIMEOUT_SECONDS, LocalAIError, call_local_ai, cli_status

app = Flask(__name__)
APP_VERSION = "1.1.0"
app.secret_key = os.urandom(24)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

@app.errorhandler(HTTPException)
def handle_http_exception(error):
    if request.path.startswith("/api/"):
        return jsonify({"error": error.description or error.name}), error.code
    return error

@app.errorhandler(Exception)
def handle_unexpected_exception(error):
    app.logger.exception("Unhandled error on %s", request.path)
    if request.path.startswith("/api/"):
        return jsonify({"error": "Error interno del servidor. Revisa los logs."}), 500
    return InternalServerError()

@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    response.headers["Referrer-Policy"] = "same-origin"
    return response

# ── CONFIG ─────────────────────────────────────────────────────
def load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

def config_value(*names):
    placeholders = {
        "",
        "TU_SPOTIFY_CLIENT_ID",
        "TU_SPOTIFY_CLIENT_SECRET",
        "TU_ANTHROPIC_KEY",
        "TU_OPENAI_KEY",
        "TU_NVIDIA_KEY",
        "your_client_id",
        "your_client_secret",
    }
    for name in names:
        value = os.environ.get(name, "").strip()
        if value and value not in placeholders:
            return value
    return ""

load_dotenv(env_file_path())

CLIENT_ID     = config_value("SPOTIFY_CLIENT_ID", "SPOTIPY_CLIENT_ID")
CLIENT_SECRET = config_value("SPOTIFY_CLIENT_SECRET", "SPOTIPY_CLIENT_SECRET")
REDIRECT_URI  = config_value("SPOTIFY_REDIRECT_URI", "SPOTIPY_REDIRECT_URI") or "http://127.0.0.1:5000/callback"
SCOPES        = "playlist-modify-public playlist-modify-private playlist-read-private playlist-read-collaborative"

ANTHROPIC_KEY = config_value("ANTHROPIC_API_KEY")
OPENAI_KEY    = config_value("OPENAI_API_KEY")
NVIDIA_KEY    = config_value("NVIDIA_API_KEY")

def _job_timeout_seconds():
    """Presupuesto total (segundos) para /api/create y /api/add_to_playlist,
    incluyendo todas las rondas de IA + resolución en Spotify. Configurable via
    PLAYLISTAI_JOB_TIMEOUT_SECONDS, con clamps para evitar valores absurdos.
    Ver CONTEXT.md ("Resolución incremental por rondas") para la relación con
    LOCAL_AI_TIMEOUT_SECONDS (local_ai.py)."""
    raw = os.environ.get("PLAYLISTAI_JOB_TIMEOUT_SECONDS", "").strip()
    try:
        value = int(raw) if raw else 360
    except ValueError:
        value = 360
    return max(120, min(value, 600))

JOB_TIMEOUT_SECONDS = _job_timeout_seconds()
# Margen extra para que el AbortController del frontend nunca dispare antes de
# que el backend haya tenido oportunidad de cerrar limpio con datos parciales.
JOB_TIMEOUT_FRONTEND_MARGIN_SECONDS = 30

@app.before_request
def normalize_loopback_host():
    redirect_host = urlparse(REDIRECT_URI).hostname
    if redirect_host in {"127.0.0.1", "::1"} and request.host.startswith("localhost:"):
        return redirect(request.url.replace("localhost:", f"{redirect_host}:"))

# Modelos disponibles por proveedor
PROVIDERS = {
    "anthropic": {
        "name": "Anthropic",
        "access": "api",
        "detail": "Usa ANTHROPIC_API_KEY",
        "models": [
            {"id": "claude-sonnet-5",    "label": "Claude Sonnet 5 (balanceado · recomendado)"},
            {"id": "claude-opus-4-8",    "label": "Claude Opus 4.8 (máxima calidad)"},
            {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5 (rápido/barato)"},
        ]
    },
    "openai": {
        "name": "OpenAI",
        "access": "api",
        "detail": "Usa OPENAI_API_KEY",
        "models": [
            {"id": "gpt-5.6-terra", "label": "GPT-5.6 Terra (balanceado · recomendado)"},
            {"id": "gpt-5.6-sol",   "label": "GPT-5.6 Sol (máxima calidad)"},
            {"id": "gpt-5.6-luna",  "label": "GPT-5.6 Luna (rápido/económico)"},
        ]
    },
    "nvidia": {
        "name": "NVIDIA NIM",
        "access": "api",
        "detail": "Usa NVIDIA_API_KEY",
        "models": [
            {"id": "deepseek-ai/deepseek-v4-pro",       "label": "DeepSeek V4 Pro (recomendado · calidad)"},
            {"id": "qwen/qwen3-next-80b-a3b-instruct",  "label": "Qwen3-Next 80B (calidad + rápido)"},
            {"id": "deepseek-ai/deepseek-v4-flash",     "label": "DeepSeek V4 Flash (más rápido)"},
            {"id": "meta/llama-3.1-70b-instruct",       "label": "Llama 3.1 70B Instruct (rápido)"},
            {"id": "nvidia/nemotron-3-ultra-550b-a55b", "label": "Nemotron 3 Ultra 550B (máx calidad, lento)"},
            {"id": "qwen/qwen3.5-397b-a17b",            "label": "Qwen 3.5 397B (VLM)"},
        ]
    },
    "claude_code": {
        "name": "Claude Code",
        "access": "subscription",
        "detail": "Usa tu sesión local de Claude Code",
        "models": [
            {"id": "default", "label": "Automático (recomendado · configuración de Claude Code)"},
            {"id": "sonnet", "label": "Sonnet (balanceado)"},
            {"id": "opus", "label": "Opus (máxima calidad)"},
            {"id": "haiku", "label": "Haiku (rápido)"},
        ],
    },
    "codex": {
        "name": "Codex",
        "access": "subscription",
        "detail": "Usa tu sesión local de ChatGPT/Codex",
        "models": [
            {"id": "default", "label": "Automático (recomendado · configuración de Codex)"},
            {"id": "gpt-5.6-terra", "label": "GPT-5.6 Terra (balanceado)"},
            {"id": "gpt-5.6-sol", "label": "GPT-5.6 Sol (máxima calidad)"},
            {"id": "gpt-5.6-luna", "label": "GPT-5.6 Luna (rápido)"},
        ],
    },
}
# ───────────────────────────────────────────────────────────────

def get_sp():
    token = session.get("token_info")
    if not token:
        return None
    # retries=0: si Spotify devuelve 429 (rate limit), que lance la excepción
    # de inmediato en vez de dormir el Retry-After (que puede ser de HORAS).
    sp = spotipy.Spotify(auth=token["access_token"], retries=0, requests_timeout=12)
    return sp

def spotify_config_error():
    missing = []
    if not CLIENT_ID:
        missing.append("SPOTIFY_CLIENT_ID")
    if not CLIENT_SECRET:
        missing.append("SPOTIFY_CLIENT_SECRET")
    if missing:
        return "Faltan credenciales reales de Spotify: " + ", ".join(missing)
    redirect_host = urlparse(REDIRECT_URI).hostname
    if redirect_host == "localhost":
        return "Spotify ya no acepta localhost como Redirect URI. Usa http://127.0.0.1:5000/callback en Spotify y en .env."
    if REDIRECT_URI == "https://oauth.pstmn.io/v1/callback":
        return "Ese Redirect URI es de Postman. Para esta app local usa http://127.0.0.1:5000/callback."
    return None

def spotify_oauth(state=None):
    return SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope=SCOPES,
        state=state,
        show_dialog=True,
        # cache_handler=None NO desactiva el cache de spotipy: la libreria lo
        # trata como "no especificado" y usa su CacheFileHandler por defecto,
        # que escribe el token en texto plano a .cache en cada login. Este
        # objeto SpotifyOAuth se crea nuevo en cada request y el token real
        # vive en session["token_info"] (ver callback()/get_sp()), asi que
        # un cache en memoria descartable es el equivalente correcto a "sin cache".
        cache_handler=MemoryCacheHandler(),
    )

def refresh_token_if_needed():
    token = session.get("token_info")
    if not token:
        return False
    if spotify_config_error():
        return False
    oauth = spotify_oauth()
    if oauth.is_token_expired(token):
        try:
            token = oauth.refresh_access_token(token["refresh_token"])
        except Exception:
            # Refresh token revocado/inválido (ej. se quitó el acceso de la app
            # en Spotify). Limpiar sesión → el frontend recibe 401 y pide reconectar.
            session.clear()
            return False
        session["token_info"] = token
    return True

def extract_playlist_id(url_or_id):
    match = re.search(r'playlist/([A-Za-z0-9]+)', url_or_id)
    return match.group(1) if match else url_or_id.strip()

def smallest_image_url(images):
    """images viene ordenado de mas grande a mas chico segun la API de Spotify."""
    images = images or []
    return images[-1]["url"] if images else None

def largest_image_url(images):
    images = images or []
    return images[0]["url"] if images else None

def get_playlist_for_analysis(sp, playlist_id):
    pl = sp.playlist(playlist_id, fields="id,name,description,images,tracks.total,owner.id,owner.display_name")
    items = []
    offset = 0
    limit = 100
    while True:
        page = sp.playlist_items(
            playlist_id,
            limit=limit,
            offset=offset,
        )
        items.extend(page.get("items", []))
        if not page.get("next"):
            break
        offset += limit
    return pl, items

def ai_config_error(provider, model=None):
    if provider not in PROVIDERS:
        return f"Proveedor de IA no soportado: {provider}"
    valid_models = {item["id"] for item in PROVIDERS[provider]["models"]}
    if model and model not in valid_models:
        return f"Modelo de IA no soportado para {PROVIDERS[provider]['name']}: {model}"
    if provider == "anthropic" and not ANTHROPIC_KEY:
        return "Falta ANTHROPIC_API_KEY en .env o selecciona otro proveedor en IA Config."
    if provider == "openai" and not OPENAI_KEY:
        return "Falta OPENAI_API_KEY en .env o selecciona otro proveedor en IA Config."
    if provider == "nvidia" and not NVIDIA_KEY:
        return "Falta NVIDIA_API_KEY en .env o selecciona otro proveedor en IA Config."
    if provider in {"claude_code", "codex"}:
        try:
            status = cli_status(provider)
        except LocalAIError as exc:
            return str(exc)
        if not status["available"]:
            return str(status["detail"])
    return None

def normalize_search_text(value):
    value = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"\([^)]*\)|\[[^]]*\]", " ", value.lower())
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()

def token_overlap_score(expected, actual):
    expected_tokens = set(normalize_search_text(expected).split())
    actual_tokens = set(normalize_search_text(actual).split())
    if not expected_tokens:
        return 0
    return len(expected_tokens & actual_tokens) / len(expected_tokens)

SPOTIFY_SEARCH_CACHE = {}
SPOTIFY_ARTIST_CACHE = {}
CACHE_MAX_ENTRIES = 500

def spotify_search(sp, query, limit=10):
    # Spotify Search API en Development Mode admite maximo 10 resultados por
    # GET /search (antes aceptaba hasta 20-50; ver CONTEXT.md). Este clamp es
    # la ultima linea de defensa aunque un caller pida mas.
    limit = max(1, min(int(limit), 10))
    cache_key = (query, limit)
    if cache_key not in SPOTIFY_SEARCH_CACHE:
        if len(SPOTIFY_SEARCH_CACHE) >= CACHE_MAX_ENTRIES:
            SPOTIFY_SEARCH_CACHE.clear()
        SPOTIFY_SEARCH_CACHE[cache_key] = sp.search(q=query, type="track", limit=limit)["tracks"]["items"]
    return SPOTIFY_SEARCH_CACHE[cache_key]

def candidate_label(track):
    artist = track["artists"][0]["name"] if track.get("artists") else "Artista desconocido"
    return f"{artist} – {track['name']}"

SPOTIFY_PLAYLIST_DESCRIPTION_MAX = 300


def normalize_playlist_description(value, fallback=""):
    """Guard de compatibilidad antes de mandar la descripción a
    `sp.current_user_playlist_create()`. Verificado en vivo (2026-08-18, ver
    CONTEXT.md): Spotify rechazó con 400 "Description exceeds limit" una
    descripción generada por la IA -- no es un límite que hayamos confirmado
    en documentación oficial de Spotify, es un guard basado en el
    comportamiento real observado de la API, con margen conservador.
    Acepta None/no-string sin fallar, normaliza whitespace, y trunca
    incluyendo el "..." dentro del límite (nunca lo supera)."""
    if not isinstance(value, str):
        value = fallback if isinstance(fallback, str) else ""
    value = re.sub(r"\s+", " ", value).strip()
    if not value:
        value = re.sub(r"\s+", " ", fallback if isinstance(fallback, str) else "").strip()
    if len(value) <= SPOTIFY_PLAYLIST_DESCRIPTION_MAX:
        return value
    ellipsis = "..."
    cutoff = SPOTIFY_PLAYLIST_DESCRIPTION_MAX - len(ellipsis)
    return value[:cutoff].rstrip() + ellipsis


NEGATION_TRIGGER_WORDS = ("evita", "evitar")
NEGATION_WINDOW_TOKENS = 6
REMIX_TERMS = ("remix", "remixes", "rmx", "mashup", "mashups")
LIVE_TERMS = ("live", "vivo", "vivos")


def _negated_within_window(tokens, terms):
    """Detecta forma negativa natural tipo "evita remixes y versiones live":
    si un token disparador ("evita"/"evitar") aparece antes de alguno de
    `terms` dentro de una ventana corta de palabras siguientes. Deliberadamente
    NO es un parser NLP genérico -- es una ventana fija sobre texto ya
    normalizado (ver `normalize_search_text`), acotada a los disparadores
    "evita"/"evitar" (no "sin"/"no", que son demasiado comunes en español para
    usarse como disparador de ventana sin arrastrar falsos positivos). Con esto
    "evita remixes y versiones live" activa remix Y live desde un solo
    disparador, pero "quiero remixes" o "prefiero grabaciones en vivo" no
    activan nada porque no hay disparador de negación presente."""
    for index, token in enumerate(tokens):
        if token in NEGATION_TRIGGER_WORDS:
            window = tokens[index + 1:index + 1 + NEGATION_WINDOW_TOKENS]
            if any(term in window for term in terms):
                return True
    return False


def detect_hard_constraints(mood):
    """Restricciones DURAS detectadas en el mood/prompt del usuario (ver
    CONTEXT.md, "Restricciones duras vs preferencias suaves"). Deben cumplirse
    literalmente en TODAS las rondas de `stream_resolve_from_prompt` y en el
    fallback de artista, sin excepción — a diferencia de las preferencias
    suaves (relajante, para trabajar, concentración, enfoque, etc.), que NO
    activan ningún filtro de rechazo y solo refuerzan el tono del prompt a la
    IA (ver `_round_prompt`).

    FIX (bug real, ver encargo): antes, "instrumental"/"sin voces" solo se
    activaba si ADEMÁS el mood mencionaba un género electrónico (techno,
    house, deep...). Un mood como "Lo-Fi / Chillhop instrumental... sin
    voces..." no calificaba y el filtro nunca se aplicaba. Ahora la detección
    de "instrumental" depende únicamente de sus propios términos.
    `electronic_context` se conserva solo como señal para decidir qué
    vocabulario ADICIONAL de rechazo aplicar en `track_allowed_by_prompt`
    (géneros no-electrónicos, acoustic/unplugged/radio edit) — nunca como
    condición para activar la restricción dura."""
    text = normalize_search_text(mood)
    tokens = text.split()
    instrumental = any(term in text for term in [
        "instrumental", "sin voces", "sin voz", "no vocal", "without vocals",
    ])
    no_remix = any(term in text for term in [
        "sin remix", "sin remixes", "no remix", "no remixes",
    ]) or _negated_within_window(tokens, REMIX_TERMS)
    no_live = any(term in text for term in [
        "sin vivo", "sin en vivo", "no live", "sin live", "no en vivo",
    ]) or _negated_within_window(tokens, LIVE_TERMS)
    electronic_context = any(term in text for term in [
        "techno", "house", "electronic", "electronica", "electronico",
        "deep", "melodic", "minimal", "progressive", "microhouse"
    ])
    return {
        "instrumental": instrumental,
        "no_remix": no_remix,
        "no_live": no_live,
        "electronic_context": electronic_context,
        "any": instrumental or no_remix or no_live,
    }

def track_allowed_by_prompt(sp, track, mood):
    """Rechaza candidatos que violen una restricción dura detectada en `mood`
    (ver `detect_hard_constraints`). LIMITACIÓN CONOCIDA (ver CONTEXT.md): la
    Search API de Spotify no expone ningún atributo confiable de
    "instrumental"/"vocal" — esto es una heurística sobre el TEXTO del
    título/álbum del candidato (`candidate_label`), no una detección real de
    audio. No se agregan llamadas nuevas a la API de Spotify."""
    constraints = detect_hard_constraints(mood)
    if not constraints["any"]:
        return True

    label = normalize_search_text(candidate_label(track))
    padded_label = f" {label} "

    rejected_terms = []
    if constraints["instrumental"]:
        # Señales de voz en el título/etiqueta — únicas disponibles sin
        # inventar capacidades que Spotify no tiene.
        rejected_terms += [
            "feat", "featuring", "ft", "vocal", "vocals", "vocal mix",
            "vocal version", "with vocals",
        ]
        if constraints["electronic_context"]:
            # Vocabulario adicional del caso original (techno/house
            # instrumental): covers acústicas/unplugged/radio edit y géneros
            # no-electrónicos que no deberían colarse en ESE pedido puntual.
            # No se aplica a moods sin contexto electrónico (ej. Lo-Fi) para
            # no sobre-filtrar géneros que sí pueden ser válidos ahí.
            rejected_terms += [
                "unplugged", "acoustic", "radio edit",
                "plena", "salsa", "bachata", "merengue", "reggaeton", "cumbia",
                "vallenato", "latin pop", "rock", "pop", "rap", "hip hop",
                "trap", "corridos", "banda", "mango", "ron",
            ]
    if constraints["no_remix"]:
        rejected_terms += ["remix", "rmx", "mashup"]
    if constraints["no_live"]:
        rejected_terms += ["live", "vivo"]

    # Coincidencia por palabra completa, no substring: "rock" no debe rechazar
    # "Rocket Man" ni "ron" rechazar "electronic".
    if any(f" {term} " in padded_label for term in rejected_terms):
        return False
    return True

def find_spotify_track(sp, name, artist=None, exclude_ids=None, mood=None, filtered_out=None):
    """`filtered_out`: lista opcional donde se registran candidatos descartados
    por el filtro del prompt (track_allowed_by_prompt), para distinguir
    "rechazada por prompt" de "no encontrada" en el resultado final."""
    exclude_ids = exclude_ids or set()
    base_name = re.sub(r"\s*[-–—]\s*(club mix|extended mix|original mix|radio edit|remix|edit)\s*$", "", name or "", flags=re.I).strip()
    # Una sola consulta por canción para no saturar el límite de Spotify.
    # "nombre artista" es robusta y suele traer el track correcto en el top 5.
    if artist:
        queries = [f"{name} {artist}"]
    else:
        queries = [name]

    best = None
    best_score = 0
    scored_ids = set()
    seen_queries = set()
    for query in queries:
        query = query.strip()
        if not query or query in seen_queries:
            continue
        seen_queries.add(query)
        for track in spotify_search(sp, query, limit=5):
            track_id = track.get("id")
            if not track_id or track_id in exclude_ids or track_id in scored_ids:
                continue
            scored_ids.add(track_id)
            if mood and not track_allowed_by_prompt(sp, track, mood):
                if filtered_out is not None:
                    filtered_out.append(candidate_label(track))
                continue
            track_artist = track["artists"][0]["name"] if track.get("artists") else ""
            all_artists = " ".join(a.get("name", "") for a in (track.get("artists") or []))
            name_score = max(token_overlap_score(name, track.get("name")), token_overlap_score(base_name, track.get("name")))

            if artist:
                # El artista debe coincidir de verdad (revisamos artista principal
                # y colaboradores). Si no coincide, descartamos: así evitamos meter
                # una canción famosa con el mismo título pero de otro artista.
                artist_score = max(token_overlap_score(artist, track_artist),
                                   token_overlap_score(artist, all_artists))
                if artist_score < 0.5 or name_score < 0.5:
                    continue
                # La popularidad ya no decide; solo el parecido real de nombre+artista.
                score = (name_score * 0.65) + (artist_score * 0.35)
            else:
                popularity = (track.get("popularity") or 0) / 100
                if name_score < 0.45:
                    continue
                score = (name_score * 0.9) + (popularity * 0.1)

            if score > best_score:
                best = track
                best_score = score

        # Salida temprana: si ya tenemos una coincidencia muy buena, no seguimos
        # buscando con las consultas siguientes (ahorra llamadas a Spotify).
        if best_score >= 0.9:
            break

    if best and best_score >= (0.6 if artist else 0.45):
        return best
    return None

def find_artist_fallback(sp, artist, exclude_ids=None, mood=None):
    """Cuando la IA inventó un título que no existe, busca una canción REAL y
    popular del MISMO artista (que respete el mood). Así la playlist se llena con
    temas reales del artista pedido en vez de quedar incompleta."""
    if not artist:
        return None
    exclude_ids = exclude_ids or set()
    best = None
    best_pop = -1
    for track in spotify_search(sp, f'artist:"{artist}"', limit=10):
        track_id = track.get("id")
        if not track_id or track_id in exclude_ids:
            continue
        track_artist = track["artists"][0]["name"] if track.get("artists") else ""
        all_artists = " ".join(a.get("name", "") for a in (track.get("artists") or []))
        artist_score = max(token_overlap_score(artist, track_artist),
                           token_overlap_score(artist, all_artists))
        if artist_score < 0.6:
            continue
        if mood and not track_allowed_by_prompt(sp, track, mood):
            continue
        pop = track.get("popularity") or 0
        if pop > best_pop:
            best = track
            best_pop = pop
    return best


def anthropic_response_text(data):
    """Extrae únicamente los bloques de texto de una respuesta Messages API."""
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, list):
        return None

    parts = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str) and text:
            parts.append(text)
    return "\n".join(parts) or None


def parse_ai_json(raw):
    """Convierte una respuesta de IA en JSON aunque venga dentro de fences o con texto extra."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("La respuesta de IA está vacía.")

    cleaned = raw.strip().lstrip("\ufeff")
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline != -1:
            cleaned = cleaned[first_newline + 1:]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3].rstrip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as original_error:
        starts = [index for index in (cleaned.find("{"), cleaned.find("[")) if index >= 0]
        if not starts:
            raise original_error
        try:
            value, _ = json.JSONDecoder().raw_decode(cleaned[min(starts):])
            return value
        except json.JSONDecodeError:
            raise original_error


def parse_playlist_json(raw):
    """Valida la forma mínima que necesita el resolvedor de canciones."""
    value = parse_ai_json(raw)
    if not isinstance(value, dict) or not isinstance(value.get("tracks"), list):
        raise ValueError("La respuesta no contiene un objeto con una lista 'tracks'.")
    tracks = value["tracks"]
    if not tracks:
        raise ValueError("La lista 'tracks' está vacía.")
    if any(
        not isinstance(track, dict)
        or not isinstance(track.get("name"), str)
        or not track["name"].strip()
        or not isinstance(track.get("artist"), str)
        or not track["artist"].strip()
        for track in tracks
    ):
        raise ValueError("Una o más canciones no contienen nombre y artista válidos.")
    return value


def call_ai(prompt, provider="anthropic", model=None, max_output_tokens=5000, output_schema=None):
    """Llama al proveedor de IA seleccionado y devuelve el texto de respuesta."""
    max_output_tokens = max(512, min(int(max_output_tokens), 16000))

    # ── Suscripciones locales ──
    if provider in {"claude_code", "codex"}:
        try:
            return call_local_ai(
                prompt,
                provider=provider,
                model=model,
                output_schema=output_schema,
            )
        except LocalAIError as exc:
            return f"Error {PROVIDERS[provider]['name']}: {exc}"

    # ── Anthropic API ──
    if provider == "anthropic":
        model = model or "claude-sonnet-5"
        payload = {
            "model": model,
            "max_tokens": max_output_tokens,
            # Sonnet 5 activa adaptive thinking por defecto. Para estas tareas
            # de JSON corto preferimos reservar todo el presupuesto al texto.
            "thinking": {"type": "disabled"},
            "messages": [{"role": "user", "content": prompt}],
        }
        if output_schema:
            payload["output_config"] = {
                "format": {"type": "json_schema", "schema": output_schema}
            }
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json=payload,
            timeout=60
        )
        data = r.json()
        if not r.ok:
            return f"Error Anthropic: {data.get('error', {}).get('message', r.text)}"
        text = anthropic_response_text(data)
        if text:
            if data.get("stop_reason") == "max_tokens":
                app.logger.warning(
                    "Anthropic response truncated at max_tokens=%s (output_tokens=%s)",
                    max_output_tokens,
                    (data.get("usage") or {}).get("output_tokens", "unknown"),
                )
            return text
        content = data.get("content") if isinstance(data, dict) else None
        blocks = content if isinstance(content, list) else []
        usage = data.get("usage") if isinstance(data, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        block_types = sorted({
            str(block.get("type", "unknown"))
            for block in blocks
            if isinstance(block, dict)
        })
        app.logger.error(
            "Anthropic response without text blocks (types=%s, stop_reason=%s, output_tokens=%s)",
            ",".join(block_types) or "none",
            data.get("stop_reason", "unknown") if isinstance(data, dict) else "unknown",
            usage.get("output_tokens", "unknown"),
        )
        return "Error Anthropic: la respuesta no incluyó ningún bloque de texto."

    # ── OpenAI ──
    elif provider == "openai":
        model = model or "gpt-5.6-terra"
        headers = {"Authorization": f"Bearer {OPENAI_KEY}", "content-type": "application/json"}
        if model.startswith("gpt-5"):
            r = requests.post(
                "https://api.openai.com/v1/responses",
                headers=headers,
                json={"model": model, "max_output_tokens": max_output_tokens, "input": prompt},
                timeout=60
            )
        else:
            r = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json={"model": model, "max_tokens": max_output_tokens,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=60
            )
        data = r.json()
        if not r.ok:
            return f"Error OpenAI: {data.get('error', {}).get('message', r.text)}"
        if "output_text" in data:
            return data["output_text"]
        if data.get("output"):
            parts = []
            for item in data["output"]:
                for content in item.get("content", []):
                    if content.get("type") in {"output_text", "text"}:
                        parts.append(content.get("text", ""))
            if parts:
                return "\n".join(parts)
        return data["choices"][0]["message"]["content"]

    # ── NVIDIA NIM ──
    elif provider == "nvidia":
        model = model or "deepseek-ai/deepseek-v4-pro"
        r = requests.post(
            "https://integrate.api.nvidia.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {NVIDIA_KEY}",
                     "content-type": "application/json"},
            json={"model": model, "max_tokens": max_output_tokens,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=60
        )
        data = r.json()
        if not r.ok:
            return f"Error NVIDIA: {data.get('detail', r.text)}"
        return data["choices"][0]["message"]["content"]

    return "Proveedor no soportado."

# ── ROUTES ─────────────────────────────────────────────────────

@app.route("/")
def index():
    logged_in = "token_info" in session
    return render_template(
        "index.html",
        logged_in=logged_in,
        auth_error=request.args.get("auth_error"),
        app_version=APP_VERSION,
        job_timeout_ms=(JOB_TIMEOUT_SECONDS + JOB_TIMEOUT_FRONTEND_MARGIN_SECONDS) * 1000,
    )

@app.route("/login")
def login():
    error = spotify_config_error()
    if error:
        return redirect("/?" + urlencode({"auth_error": error}))
    state = secrets.token_urlsafe(24)
    session["spotify_auth_state"] = state
    return redirect(spotify_oauth(state=state).get_authorize_url())

@app.route("/callback")
def callback():
    error = spotify_config_error()
    if error:
        return redirect("/?" + urlencode({"auth_error": error}))
    spotify_error = request.args.get("error")
    if spotify_error:
        return redirect("/?" + urlencode({"auth_error": f"Spotify canceló el acceso: {spotify_error}"}))
    expected_state = session.pop("spotify_auth_state", None)
    received_state = request.args.get("state")
    if not expected_state or received_state != expected_state:
        return redirect("/?" + urlencode({"auth_error": "La sesión de autenticación expiró. Intenta conectar de nuevo."}))
    code = request.args.get("code")
    if not code:
        return redirect("/?" + urlencode({"auth_error": "Spotify no devolvió un código de autorización."}))
    token_info = spotify_oauth().get_access_token(code, as_dict=True)
    session["token_info"] = token_info
    return redirect("/")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/api/me")
def api_me():
    if not refresh_token_if_needed():
        return jsonify({"error": "not_logged_in"}), 401
    sp = get_sp()
    user = sp.me()
    return jsonify({"name": user["display_name"], "image": user["images"][0]["url"] if user.get("images") else None})

@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    if not refresh_token_if_needed():
        return jsonify({"error": "not_logged_in"}), 401
    sp = get_sp()
    data = request.json
    playlist_id = extract_playlist_id(data.get("url", ""))

    # Fetch playlist info
    try:
        pl, tracks = get_playlist_for_analysis(sp, playlist_id)
    except SpotifyException as e:
        status = getattr(e, "http_status", None)
        if status == 404:
            return jsonify({"error": "Spotify no encontró esa playlist (404). Suele pasar con las playlists creadas por Spotify (Discover Weekly, Top 50, Radar, 'This Is…', Daily Mix): Spotify bloquea su acceso por API para apps en modo desarrollo. Usa una playlist tuya o de otro usuario, con su link completo (open.spotify.com/playlist/...)."}), 400
        if status == 401:
            return jsonify({"error": "Tu sesión de Spotify expiró. Dale 'Salir' y vuelve a conectar."}), 401
        return jsonify({"error": f"No se pudo cargar la playlist: {str(e)}"}), 400
    except Exception as e:
        return jsonify({"error": f"No se pudo cargar la playlist: {str(e)}"}), 400

    track_list = []
    for item in tracks:
        # Spotify a veces devuelve la canción bajo "track" y a veces bajo "item".
        track = None
        if isinstance(item, dict):
            track = item.get("track") or item.get("item")
        if not isinstance(track, dict):
            continue
        uri = track.get("uri")
        name = track.get("name")
        if not uri or not name:
            continue
        artists = track.get("artists") or []
        artist = "Artista desconocido"
        if artists and isinstance(artists[0], dict):
            artist = artists[0].get("name") or artist
        track_list.append({
            "id": track.get("id") or "",
            "name": name,
            "artist": artist,
            "uri": uri,
            "image": smallest_image_url((track.get("album") or {}).get("images")),
        })
    if not track_list:
        total = pl.get("tracks", {}).get("total", 0)
        owner_id = (pl.get("owner") or {}).get("id", "")
        if owner_id == "spotify":
            return jsonify({"error": "Esta playlist es creada por Spotify (editorial/algorítmica) y Spotify bloquea su contenido por API para apps en modo desarrollo, por eso llega vacía. Analiza una playlist tuya o de otro usuario."}), 400
        if total == 0:
            return jsonify({"error": "La playlist está vacía (0 canciones). Analiza una que tenga canciones agregadas."}), 400
        return jsonify({"error": f"Spotify devolvió {total} items pero ninguno era una canción reproducible (pueden ser episodios de podcast, archivos locales o no disponibles en tu país). Prueba con otra playlist."}), 400

    # Ask Claude to analyze
    ai_track_sample = track_list[:120]
    names = "\n".join([f"- {t['artist']} – {t['name']}" for t in ai_track_sample])
    prompt = f"""Eres un experto en curaduría musical. Analiza esta playlist de Spotify:

Título: "{pl['name']}"
Descripción: "{pl.get('description', 'Sin descripción')}"

Canciones actuales:
{names}

Tareas:
1. Evalúa qué tan bien encaja cada canción con el título/descripción. Identifica las que NO encajan (máximo 3).
2. Sugiere exactamente 8 canciones nuevas que encajen perfectamente con el concepto. Para cada una: artista exacto, título exacto.
3. Da un resumen breve del análisis.

Responde SOLO en este formato JSON (sin markdown, sin texto extra):
{{
  "summary": "Resumen del análisis en 2 oraciones",
  "remove": [
    {{"id": "ID_SPOTIFY_O_EMPTY", "name": "Nombre canción", "artist": "Artista", "reason": "Por qué no encaja"}}
  ],
  "add": [
    {{"name": "Nombre exacto", "artist": "Artista exacto", "reason": "Por qué encaja"}}
  ]
}}"""

    provider = data.get("provider", "anthropic")
    model    = data.get("model", None)
    ai_error = ai_config_error(provider, model)
    if ai_error:
        return jsonify({"error": ai_error}), 400
    ai_raw = call_ai(prompt, provider=provider, model=model)
    try:
        ai = parse_ai_json(ai_raw)
    except Exception:
        ai = {"summary": ai_raw, "remove": [], "add": []}
    if not isinstance(ai, dict):
        ai = {"summary": ai_raw, "remove": [], "add": []}

    # Match remove suggestions with actual track IDs from playlist.
    # La respuesta de la IA es JSON no confiable: puede venir sin "name"/"artist"
    # o con items que no son dicts — usar .get() para no tirar 500.
    remove_items = ai.get("remove") if isinstance(ai.get("remove"), list) else []
    ai["remove"] = [r for r in remove_items if isinstance(r, dict)]
    for r_item in ai["remove"]:
        r_name = str(r_item.get("name") or "").lower()
        r_artist = str(r_item.get("artist") or "").lower()
        if not r_name:
            continue
        for t in track_list:
            if r_name in t["name"].lower() and r_artist in t["artist"].lower():
                r_item["id"] = t["id"]
                r_item["uri"] = t["uri"]
                r_item["image"] = t.get("image")
                break

    return jsonify({
        "playlist_id": playlist_id,
        "playlist_name": pl["name"],
        "playlist_description": pl.get("description", ""),
        "playlist_image": largest_image_url(pl.get("images")),
        "tracks": track_list,
        "ai": ai
    })

@app.route("/api/apply", methods=["POST"])
def api_apply():
    if not refresh_token_if_needed():
        return jsonify({"error": "not_logged_in"}), 401
    sp = get_sp()
    data = request.json
    playlist_id = data.get("playlist_id")
    to_remove = data.get("remove", [])
    to_add_raw = data.get("add", [])

    results = {"removed": [], "added": [], "not_found": []}

    # Remove tracks
    uris_to_remove = [t["uri"] for t in to_remove if t.get("uri")]
    if uris_to_remove:
        try:
            sp.playlist_remove_all_occurrences_of_items(playlist_id, uris_to_remove)
        except SpotifyException as e:
            if getattr(e, "http_status", None) == 403:
                return jsonify({"error": "Spotify rechazó eliminar canciones (403). Solo puedes editar playlists tuyas o colaborativas con permisos, y debes reconectar aceptando permisos."}), 403
            return jsonify({"error": f"No se pudieron eliminar canciones: {str(e)}"}), 400
        results["removed"] = [f"{t['artist']} – {t['name']}" for t in to_remove if t.get("uri")]

    # Search and add tracks
    track_ids_to_add = []
    selected_ids = set()
    for item in to_add_raw:
        if not isinstance(item, dict):
            continue
        track = find_spotify_track(sp, item.get("name"), item.get("artist"), selected_ids)
        if track:
            selected_ids.add(track["id"])
            track_ids_to_add.append(track["id"])
            results["added"].append(candidate_label(track))
        else:
            results["not_found"].append(f"{item.get('artist', 'Artista desconocido')} – {item.get('name', 'Canción desconocida')}")

    if track_ids_to_add:
        try:
            sp.playlist_add_items(playlist_id, track_ids_to_add)
        except SpotifyException as e:
            if getattr(e, "http_status", None) == 403:
                return jsonify({"error": "Spotify rechazó agregar canciones (403). Solo puedes editar playlists tuyas o colaborativas con permisos, y debes reconectar aceptando permisos."}), 403
            return jsonify({"error": f"No se pudieron agregar canciones: {str(e)}"}), 400

    return jsonify(results)

def _event(obj):
    return json.dumps(obj, ensure_ascii=False) + "\n"


BATCH_MIN_SIZE = 6   # por debajo de esto no vale la pena gastar una llamada a IA
BATCH_MAX_SIZE = 18  # tope de candidatos por ronda (lotes chicos = IA mas rapida/confiable)
NO_PROGRESS_ROUND_LIMIT = 2  # rondas seguidas sin sumar ninguna cancion -> se corta


def _round_batch_size(needed):
    """Oversampling dinamico: cuantos candidatos pedirle a la IA para cubrir
    `needed` canciones que aun faltan. ceil(needed*1.4) como base, con piso
    (que valga la pena la llamada) y techo (lotes chicos, rapidos y faciles de
    parsear) — nunca crece sin control ronda tras ronda porque siempre se
    recalcula sobre `needed`, no sobre un acumulado."""
    if needed <= 0:
        return 0
    target = math.ceil(needed * 1.4)
    return max(min(target, BATCH_MAX_SIZE), BATCH_MIN_SIZE)


def _max_rounds_for_count(count):
    """Tope estricto de rondas de IA para un pedido de `count` canciones. Crece
    con el tamaño del pedido (piden mas -> se permiten mas rondas) pero siempre
    con techo fijo para no loopear indefinidamente."""
    return max(4, min(12, math.ceil(count / 5) + 2))


def _round_time_budget_seconds(provider):
    """Estimacion (con margen) de cuanto puede tardar UNA ronda completa (llamada
    a IA + resolucion en Spotify), usada para decidir si arrancar una ronda mas
    o cortar por deadline ANTES de empezarla. Se basa en el timeout real de la
    llamada a IA, no en un numero inventado: LOCAL_AI_TIMEOUT_SECONDS para
    suscripciones locales (Claude Code/Codex, procesos mas lentos) o el timeout
    de 60s de `requests` para las APIs (Anthropic/OpenAI/NVIDIA) en `call_ai()`."""
    base = LOCAL_AI_TIMEOUT_SECONDS if provider in {"claude_code", "codex"} else 60
    return base + 15  # margen para la resolucion en Spotify (paralela, normalmente rapida)


def _normalize_track_key(name, artist):
    name_n = normalize_search_text(name)
    artist_n = normalize_search_text(artist)
    if not name_n and not artist_n:
        return None
    return f"{artist_n}|{name_n}"


def _round_prompt(name, mood, count, batch_size, round_index, accepted, attempted, constraints=None):
    """Prompt unico para cada ronda del loop incremental. Reemplaza a los
    antiguos _create_prompt/_retry_prompt/_repair_prompt: round_index == 0 pide
    la tanda inicial (con descripcion), las siguientes piden solo reemplazos y
    reciben explicitamente todo lo ya aceptado/intentado para no repetirlo.

    `constraints` (dict de `detect_hard_constraints`, calculado UNA VEZ al
    inicio del job en `stream_resolve_from_prompt` — el mood no cambia entre
    rondas) refuerza literalmente las restricciones duras detectadas en TODAS
    las rondas, no solo en la ronda 0, para que la IA no las "olvide" al pedir
    reemplazos."""
    if constraints is None:
        constraints = detect_hard_constraints(mood)
    context = (
        f'Título: "{name}"\n'
        f'Descripción/Mood: "{mood}"\n'
        f"Cantidad final deseada: {count}"
    )

    if round_index == 0:
        task = (
            f"Genera {batch_size} canciones candidatas para que el sistema pueda "
            f"verificar disponibilidad en Spotify y quedarse con las mejores {count}."
        )
        schema_hint = (
            '{\n  "description": "Descripción corta de la playlist, 1 oración, '
            'máximo 240 caracteres",'
            '\n  "tracks": [\n    {"name": "Nombre exacto de la canción", "artist": "Artista exacto"}\n  ]\n}'
        )
    else:
        accepted_block = json.dumps(accepted, ensure_ascii=False) if accepted else "[]"
        attempted_block = json.dumps(attempted[-60:], ensure_ascii=False) if attempted else "[]"
        task = (
            "Ya se aceptaron estas canciones reales de Spotify para esta playlist "
            f"(no las repitas):\n{accepted_block}\n\n"
            "Estas otras sugerencias YA SE INTENTARON en rondas anteriores — no se "
            "encontraron en Spotify, fueron rechazadas por no cumplir el prompt, o "
            "eran duplicados. NO LAS REPITAS bajo ningún concepto, ni con el "
            f"título/artista exacto ni con variaciones obvias:\n{attempted_block}\n\n"
            f"Necesito {batch_size} canciones NUEVAS, reales y distintas a todo lo anterior."
        )
        schema_hint = '{\n  "tracks": [\n    {"name": "Nombre exacto de la canción", "artist": "Artista exacto"}\n  ]\n}'

    hard_constraint_notes = []
    if constraints.get("instrumental"):
        hard_constraint_notes.append(
            "- INSTRUMENTAL / SIN VOCES: ninguna canción puede tener voces, coros, "
            "raps ni letra cantada. Rechaza cualquier candidato con voz, incluso si "
            "encaja perfecto en género o mood."
        )
    if constraints.get("no_remix"):
        hard_constraint_notes.append("- SIN REMIXES: no incluyas remixes, mashups ni versiones remixadas.")
    if constraints.get("no_live"):
        hard_constraint_notes.append("- SIN GRABACIONES EN VIVO: no incluyas versiones live/en vivo.")
    hard_constraint_block = (
        "\n\nRESTRICCIONES OBLIGATORIAS de esta playlist (detectadas en el mood original):\n"
        + "\n".join(hard_constraint_notes)
        if hard_constraint_notes else ""
    )

    return f"""Eres un experto DJ y curador musical. El usuario quiere crear una playlist con este concepto:

{context}

{task}
Deben ser canciones reales, oficiales y disponibles en Spotify.
Respeta literalmente restricciones del usuario como instrumental, sin voces, género, BPM, mood, época, idioma, país, energía o artistas de referencia.
Si el usuario menciona programar, enfoque, concentración, relajante, estudiar, deep, smooth, mentalidad o manifestación, prioriza canciones hipnóticas, limpias, repetitivas y de energía baja/media; evita tracks agresivos, ruidosos, industriales, peak-time, rave, acid o demasiado intensos aunque pertenezcan al género pedido.
Evita inventar remixes, bootlegs, edits, club mixes o títulos raros si no estás seguro de que existen oficialmente. Prefiere releases oficiales, fáciles de encontrar por búsqueda de Spotify. Varía los artistas — no repitas más de 2 canciones del mismo artista en total.
Las restricciones obligatorias del prompt original permanecen vigentes en esta ronda. No las relajes para alcanzar la cantidad solicitada. Es preferible devolver menos canciones que violarlas.{hard_constraint_block}

Responde SOLO en este formato JSON (sin markdown, sin texto extra):
{schema_hint}"""


def stream_resolve_from_prompt(sp, name, mood, count, provider, model, deadline, preselected_ids, result):
    """Generador que pide canciones a la IA en rondas incrementales y las
    resuelve en Spotify, emitiendo eventos NDJSON de status/progress. Llena
    `result` (dict con listas) y `result["stop_reason"]` con el motivo de
    parada ("completed", "max_rounds", "no_progress", "deadline" o "fatal").
    En caso de error terminal escribe result["fatal"]["error"] y termina; el
    llamador decide qué hacer. `preselected_ids` son IDs que NO se deben volver
    a agregar (ya existen en la playlist o en esta misma corrida)."""
    track_ids = result["track_ids"]
    added = result["added"]
    resolved_tracks = result["resolved_tracks"]
    not_found = result["not_found"]
    rejected = result["rejected"]
    replacement_added = result["replacement_added"]
    substitutes = result["substitutes"]
    fatal = result["fatal"]
    selected_ids = set(preselected_ids)  # incluye lo ya existente, para no duplicar
    attempted_normalized = set()  # "artist|name" normalizado: historial completo del job
    attempted_labels = []
    # Restricciones duras calculadas UNA VEZ a partir del mood original del job
    # (no se re-derivan por ronda: el mood no cambia entre rondas). Se pasan
    # explícitamente a `_round_prompt` para reforzar la instrucción en cada
    # ronda. `track_allowed_by_prompt` recalcula lo mismo internamente a partir
    # de este mismo `mood` invariable (cómputo puro y barato) para no romper
    # la firma de `find_spotify_track`/`find_artist_fallback` — mismo
    # resultado, sin acoplar más funciones a un parámetro nuevo.
    hard_constraints = detect_hard_constraints(mood)

    def remember_attempt(label, item_name, item_artist):
        key = _normalize_track_key(item_name, item_artist)
        if key:
            attempted_normalized.add(key)
        if label not in attempted_labels:
            attempted_labels.append(label)

    def resolve_items(items, mark_replacement=False):
        """Busca las sugerencias en Spotify EN PARALELO y emite progreso en orden."""
        items = [it for it in items if it]
        if not items or len(track_ids) >= count or fatal:
            return
        exclude_snapshot = set(selected_ids)

        def resolve_one(it):
            # 1) intento exacto (título + artista). 2) si la IA inventó el título,
            # respaldo con una canción real del mismo artista.
            filtered = []
            track = find_spotify_track(sp, it.get("name"), it.get("artist"), exclude_snapshot, mood, filtered_out=filtered)
            if track:
                return (track, False, False)
            if it.get("artist"):
                fb = find_artist_fallback(sp, it.get("artist"), exclude_snapshot, mood)
                if fb:
                    return (fb, True, False)
            # was_rejected: se encontró en Spotify pero el filtro del prompt la descartó.
            return (None, False, bool(filtered))

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(resolve_one, it) for it in items]
            for it, fut in zip(items, futures):
                if len(track_ids) >= count or time.monotonic() > deadline:
                    for f in futures:
                        f.cancel()
                    break
                try:
                    track, is_fallback, was_rejected = fut.result()
                except SpotifyException as e:
                    if getattr(e, "http_status", None) == 429:
                        # reason viene del body de error de Spotify cuando lo incluye
                        # (spotipy lo expone en SpotifyException.reason); QUOTA_EXCEEDED
                        # indica un limite de cuota de la app, no un rate limit pasajero
                        # de peticiones — vale la pena distinguirlo para el usuario.
                        if getattr(e, "reason", None) == "QUOTA_EXCEEDED":
                            fatal["error"] = ("Spotify indica que esta app alcanzó su cuota "
                                              "(QUOTA_EXCEEDED), no un límite temporal de "
                                              "peticiones. Revisa el modo/cuota de la app en "
                                              "el Developer Dashboard de Spotify.")
                        else:
                            fatal["error"] = ("Spotify limitó las peticiones (429) por demasiadas "
                                              "búsquedas seguidas. Espera unos minutos e intenta de nuevo.")
                        for f in futures:
                            f.cancel()
                        break
                    track, is_fallback, was_rejected = None, False, False
                except Exception:
                    track, is_fallback, was_rejected = None, False, False
                suggestion_label = f"{it.get('artist', 'Artista desconocido')} – {it.get('name', 'Canción desconocida')}"
                if track and track["id"] not in selected_ids:
                    selected_ids.add(track["id"])
                    track_ids.append(track["id"])
                    label = candidate_label(track)
                    added.append(label)
                    resolved_tracks.append({
                        "id": track["id"],
                        "label": label,
                        "image": smallest_image_url((track.get("album") or {}).get("images")),
                    })
                    if mark_replacement:
                        replacement_added.append(label)
                    if is_fallback:
                        substitutes.append(label)
                    remember_attempt(label, it.get("name"), it.get("artist"))
                    yield _event({"type": "progress", "done": len(track_ids), "total": count, "label": label})
                elif not track:
                    if was_rejected:
                        rejected.append(suggestion_label)
                    else:
                        not_found.append(suggestion_label)
                    remember_attempt(suggestion_label, it.get("name"), it.get("artist"))
                else:
                    # Se encontró en Spotify pero el ID ya estaba en la playlist/en esta
                    # corrida (dos sugerencias distintas de la IA resolvieron al mismo
                    # track) — igual se recuerda para que la IA no lo vuelva a proponer.
                    remember_attempt(suggestion_label, it.get("name"), it.get("artist"))

    rounds = 0
    consecutive_no_progress = 0
    max_rounds = _max_rounds_for_count(count)
    round_budget = _round_time_budget_seconds(provider)
    stop_reason = None

    while len(track_ids) < count:
        if rounds >= max_rounds:
            stop_reason = "max_rounds"
            break
        if consecutive_no_progress >= NO_PROGRESS_ROUND_LIMIT:
            stop_reason = "no_progress"
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # Ya no queda tiempo: ni siquiera vale la pena intentar otra ronda.
            stop_reason = "deadline"
            break
        if rounds > 0 and remaining < round_budget:
            # La ronda 0 siempre se intenta al menos una vez (mejor un resultado
            # parcial que ninguno); a partir de la 2da ronda exigimos margen
            # suficiente para terminarla antes del deadline real.
            stop_reason = "deadline"
            break

        needed = count - len(track_ids)
        batch_size = _round_batch_size(needed)
        is_first_round = rounds == 0
        if is_first_round:
            yield _event({"type": "status", "message": "La IA está eligiendo las canciones…"})
        else:
            yield _event({"type": "status", "message": f"Completando la lista ({len(track_ids)}/{count})… ronda {rounds + 1}"})

        prompt = _round_prompt(name, mood, count, batch_size, rounds, added, attempted_labels, hard_constraints)
        token_budget = min(12000, max(4000, batch_size * 220))
        ai_raw = call_ai(
            prompt,
            provider=provider,
            model=model,
            max_output_tokens=token_budget,
            output_schema=_playlist_output_schema(include_description=is_first_round),
        )
        try:
            ai = parse_playlist_json(ai_raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            app.logger.warning(
                "Invalid playlist JSON; retrying once (round=%s, provider=%s, model=%s, chars=%s)",
                rounds,
                provider,
                model or "default",
                len(ai_raw) if isinstance(ai_raw, str) else 0,
            )
            second_prompt = (
                prompt
                + "\n\nIMPORTANTE: El intento anterior produjo JSON incompleto o inválido. "
                  "Devuelve el objeto completo, compacto y correctamente cerrado."
            )
            ai_raw = call_ai(
                second_prompt,
                provider=provider,
                model=model,
                max_output_tokens=min(16000, token_budget + 3000),
                output_schema=_playlist_output_schema(include_description=is_first_round),
            )
            try:
                ai = parse_playlist_json(ai_raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                if is_first_round and not track_ids:
                    # Sin nada aceptado todavía y la ronda inicial falló dos veces:
                    # no hay nada útil que mostrar, se corta como error fatal.
                    fatal["error"] = (
                        "La IA devolvió una lista incompleta o inválida después de dos intentos. "
                        f"Respuesta: {str(ai_raw)[:200]}"
                    )
                    return
                # Ya hay progreso previo (o esto no es la ronda inicial): tratar como
                # ronda sin candidatos en vez de tirar todo — el loop de rondas decide
                # si vale la pena seguir intentando (max_rounds / no_progress).
                ai = {"tracks": []}

        if is_first_round:
            result["description"] = normalize_playlist_description(ai.get("description"), fallback=mood)
            yield _event({"type": "status", "message": "Verificando canciones en Spotify…"})

        candidates = ai.get("tracks", []) if isinstance(ai.get("tracks"), list) else []
        # Filtrar duplicados/ya-intentados ANTES de gastar búsquedas de Spotify.
        filtered_candidates = []
        seen_this_round = set()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            key = _normalize_track_key(candidate.get("name"), candidate.get("artist"))
            if not key or key in attempted_normalized or key in seen_this_round:
                continue
            seen_this_round.add(key)
            filtered_candidates.append(candidate)

        before_count = len(track_ids)
        yield from resolve_items(filtered_candidates, mark_replacement=not is_first_round)
        if fatal:
            return

        progress = len(track_ids) - before_count
        consecutive_no_progress = 0 if progress > 0 else consecutive_no_progress + 1
        rounds += 1

    if stop_reason is None:
        stop_reason = "completed" if len(track_ids) >= count else "unknown"
    result["stop_reason"] = stop_reason


def _new_result(mood):
    return {
        "track_ids": [], "added": [], "resolved_tracks": [], "not_found": [],
        "rejected": [], "replacement_added": [], "substitutes": [],
        "fatal": {}, "description": mood, "stop_reason": None,
    }


def _ndjson_response(generator):
    return Response(
        stream_with_context(generator),
        mimetype="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/create", methods=["POST"])
def api_create():
    if not refresh_token_if_needed():
        return jsonify({"error": "not_logged_in"}), 401
    sp = get_sp()
    data = request.json
    mood = data.get("mood", "")
    name = data.get("name", "Mi Playlist IA")
    try:
        count = max(5, min(int(data.get("count", 15)), 50))
    except (TypeError, ValueError):
        return jsonify({"error": "Cantidad de canciones inválida."}), 400
    deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
    provider = data.get("provider", "anthropic")
    model    = data.get("model", None)
    ai_error = ai_config_error(provider, model)
    if ai_error:
        return jsonify({"error": ai_error}), 400

    def generate():
        result = _new_result(mood)
        try:
            yield from stream_resolve_from_prompt(sp, name, mood, count, provider, model, deadline, set(), result)
            if result["fatal"]:
                yield _event({"type": "error", "error": result["fatal"]["error"]})
                return
            if not result["track_ids"]:
                yield _event({"type": "error", "error": "No se encontró ninguna canción real en Spotify para crear la playlist. Prueba un mood más concreto o menos restrictivo."})
                return

            yield _event({"type": "status", "message": "Creando la playlist en tu Spotify…"})
            try:
                pl = sp.current_user_playlist_create(
                    name=name, public=False,
                    description=normalize_playlist_description(result["description"], fallback=mood),
                )
            except SpotifyException as e:
                if getattr(e, "http_status", None) == 403:
                    yield _event({"type": "error", "error": "Spotify rechazó crear la playlist (403). Cierra sesión con 'Salir', vuelve a conectar Spotify y acepta los permisos de modificación de playlists."})
                    return
                yield _event({"type": "error", "error": f"No se pudo crear la playlist en Spotify: {str(e)}"})
                return

            playlist_id = pl["id"]
            playlist_url = pl["external_urls"]["spotify"]
            try:
                sp.playlist_add_items(playlist_id, result["track_ids"])
            except SpotifyException as e:
                if getattr(e, "http_status", None) == 403:
                    yield _event({"type": "error", "error": "La playlist se creó, pero Spotify rechazó agregar canciones (403). Reconecta Spotify y acepta permisos."})
                    return
                yield _event({"type": "error", "error": f"No se pudieron agregar canciones en Spotify: {str(e)}"})
                return

            yield _event({
                "type": "done",
                "playlist_id": playlist_id,
                "playlist_url": playlist_url,
                "playlist_name": name,
                "added": result["added"],
                "resolved_tracks": result["resolved_tracks"],
                "replacement_added": result["replacement_added"],
                "substitutes": result["substitutes"],
                "not_found": result["not_found"],
                "rejected": result["rejected"],
                "target_count": count,
                "timed_out": result["stop_reason"] == "deadline" or (
                    len(result["track_ids"]) < count and time.monotonic() >= deadline
                ),
                "stop_reason": result["stop_reason"],
            })
        except Exception as e:
            app.logger.exception("Error en /api/create stream")
            yield _event({"type": "error", "error": f"Error interno: {str(e)}"})

    return _ndjson_response(generate())


@app.route("/api/add_to_playlist", methods=["POST"])
def api_add_to_playlist():
    """Agrega canciones nuevas (según un prompt) a una playlist ya existente."""
    if not refresh_token_if_needed():
        return jsonify({"error": "not_logged_in"}), 401
    sp = get_sp()
    data = request.json
    mood = data.get("mood", "")
    playlist_id = extract_playlist_id(data.get("url", ""))
    try:
        count = max(1, min(int(data.get("count", 10)), 50))
    except (TypeError, ValueError):
        return jsonify({"error": "Cantidad de canciones inválida."}), 400
    deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
    provider = data.get("provider", "anthropic")
    model    = data.get("model", None)
    ai_error = ai_config_error(provider, model)
    if ai_error:
        return jsonify({"error": ai_error}), 400

    def generate():
        result = _new_result(mood)
        try:
            # Cargar la playlist existente y sus canciones (para no duplicar).
            try:
                pl_info, existing_items = get_playlist_for_analysis(sp, playlist_id)
            except SpotifyException as e:
                status = getattr(e, "http_status", None)
                if status == 404:
                    yield _event({"type": "error", "error": "No se encontró esa playlist (404). Si es una playlist creada por Spotify, no se puede editar por API. Usa una playlist tuya."})
                    return
                yield _event({"type": "error", "error": f"No se pudo cargar la playlist: {str(e)}"})
                return
            except Exception as e:
                yield _event({"type": "error", "error": f"No se pudo cargar la playlist: {str(e)}"})
                return

            playlist_name = pl_info.get("name", "tu playlist")
            existing_ids = set()
            for it in existing_items:
                tr = (it.get("track") or it.get("item")) if isinstance(it, dict) else None
                if isinstance(tr, dict) and tr.get("id"):
                    existing_ids.add(tr["id"])

            yield from stream_resolve_from_prompt(sp, playlist_name, mood, count, provider, model, deadline, existing_ids, result)
            if result["fatal"]:
                yield _event({"type": "error", "error": result["fatal"]["error"]})
                return
            if not result["track_ids"]:
                yield _event({"type": "error", "error": "No se encontró ninguna canción nueva para agregar. Prueba un prompt distinto."})
                return

            yield _event({"type": "status", "message": f"Agregando canciones a “{playlist_name}”…"})
            try:
                sp.playlist_add_items(playlist_id, result["track_ids"])
            except SpotifyException as e:
                if getattr(e, "http_status", None) == 403:
                    yield _event({"type": "error", "error": "Spotify rechazó agregar canciones (403). Solo puedes editar playlists tuyas. Reconecta y acepta permisos."})
                    return
                yield _event({"type": "error", "error": f"No se pudieron agregar canciones: {str(e)}"})
                return

            yield _event({
                "type": "done",
                "playlist_id": playlist_id,
                "playlist_url": f"https://open.spotify.com/playlist/{playlist_id}",
                "playlist_name": playlist_name,
                "added": result["added"],
                "resolved_tracks": result["resolved_tracks"],
                "replacement_added": result["replacement_added"],
                "substitutes": result["substitutes"],
                "not_found": result["not_found"],
                "rejected": result["rejected"],
                "target_count": count,
                "timed_out": result["stop_reason"] == "deadline" or (
                    len(result["track_ids"]) < count and time.monotonic() >= deadline
                ),
                "stop_reason": result["stop_reason"],
            })
        except Exception as e:
            app.logger.exception("Error en /api/add_to_playlist stream")
            yield _event({"type": "error", "error": f"Error interno: {str(e)}"})

    return _ndjson_response(generate())


def _playlist_output_schema(include_description):
    """Esquema compacto compartido por la generación inicial y sus reemplazos."""
    track_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "artist": {"type": "string"},
        },
        "required": ["name", "artist"],
        "additionalProperties": False,
    }
    properties = {
        "tracks": {
            "type": "array",
            "items": track_schema,
        }
    }
    required = ["tracks"]
    if include_description:
        properties = {
            "description": {"type": "string"},
            **properties,
        }
        required = ["description", "tracks"]
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


@app.route("/api/providers")
def api_providers():
    available = {}
    for key, prov in PROVIDERS.items():
        if prov["access"] == "subscription":
            try:
                status = cli_status(key)
            except LocalAIError as exc:
                status = {
                    "installed": True,
                    "authenticated": False,
                    "available": False,
                    "detail": str(exc),
                }
            available[key] = {**prov, **status}
            continue
        has_key = bool(
            (key == "anthropic" and ANTHROPIC_KEY) or
            (key == "openai"    and OPENAI_KEY)    or
            (key == "nvidia"    and NVIDIA_KEY)
        )
        available[key] = {
            **prov,
            "available": has_key,
            "installed": True,
            "authenticated": has_key,
            "detail": prov["detail"] if has_key else "Falta la API key en .env",
        }
    return jsonify(available)

if __name__ == "__main__":
    app.run(debug=False, use_reloader=False, port=5000)
