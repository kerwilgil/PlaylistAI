import os, re, json, secrets, time, unicodedata, requests
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode, urlparse
from flask import Flask, redirect, request, session, jsonify, render_template, Response, stream_with_context
from werkzeug.exceptions import HTTPException, InternalServerError
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException
from spotipy.cache_handler import MemoryCacheHandler
import spotipy

app = Flask(__name__)
app.secret_key = os.urandom(24)

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

load_dotenv()

CLIENT_ID     = config_value("SPOTIFY_CLIENT_ID", "SPOTIPY_CLIENT_ID")
CLIENT_SECRET = config_value("SPOTIFY_CLIENT_SECRET", "SPOTIPY_CLIENT_SECRET")
REDIRECT_URI  = config_value("SPOTIFY_REDIRECT_URI", "SPOTIPY_REDIRECT_URI") or "http://127.0.0.1:5000/callback"
SCOPES        = "playlist-modify-public playlist-modify-private playlist-read-private playlist-read-collaborative"

ANTHROPIC_KEY = config_value("ANTHROPIC_API_KEY")
OPENAI_KEY    = config_value("OPENAI_API_KEY")
NVIDIA_KEY    = config_value("NVIDIA_API_KEY")

@app.before_request
def normalize_loopback_host():
    redirect_host = urlparse(REDIRECT_URI).hostname
    if redirect_host in {"127.0.0.1", "::1"} and request.host.startswith("localhost:"):
        return redirect(request.url.replace("localhost:", f"{redirect_host}:"))

# Modelos disponibles por proveedor
PROVIDERS = {
    "anthropic": {
        "name": "Anthropic",
        "models": [
            {"id": "claude-opus-4-8",    "label": "Claude Opus 4.8 (máx calidad · recomendado)"},
            {"id": "claude-sonnet-4-6",  "label": "Claude Sonnet 4.6 (balanceado)"},
            {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5 (rápido/barato)"},
        ]
    },
    "openai": {
        "name": "OpenAI",
        "models": [
            {"id": "gpt-5.4",       "label": "Calidad alta · GPT-5.4 (recomendado)"},
            {"id": "gpt-5.5",       "label": "Máxima calidad · GPT-5.5 (más lento)"},
            {"id": "gpt-5.4-mini",  "label": "Balanceado · GPT-5.4 Mini"},
            {"id": "gpt-5.4-nano",  "label": "Económico · GPT-5.4 Nano (mínimo costo / rápido)"},
            {"id": "gpt-4.1",       "label": "Legacy calidad · GPT-4.1"},
            {"id": "gpt-4.1-mini",  "label": "Legacy balanceado · GPT-4.1 Mini"},
            {"id": "gpt-4.1-nano",  "label": "Legacy económico · GPT-4.1 Nano"},
        ]
    },
    "nvidia": {
        "name": "NVIDIA NIM",
        "models": [
            {"id": "deepseek-ai/deepseek-v4-pro",       "label": "DeepSeek V4 Pro (recomendado · calidad)"},
            {"id": "qwen/qwen3-next-80b-a3b-instruct",  "label": "Qwen3-Next 80B (calidad + rápido)"},
            {"id": "deepseek-ai/deepseek-v4-flash",     "label": "DeepSeek V4 Flash (más rápido)"},
            {"id": "meta/llama-3.1-70b-instruct",       "label": "Llama 3.1 70B Instruct (rápido)"},
            {"id": "nvidia/nemotron-3-ultra-550b-a55b", "label": "Nemotron 3 Ultra 550B (máx calidad, lento)"},
            {"id": "qwen/qwen3.5-397b-a17b",            "label": "Qwen 3.5 397B (VLM)"},
        ]
    }
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
        token = oauth.refresh_access_token(token["refresh_token"])
        session["token_info"] = token
    return True

def extract_playlist_id(url_or_id):
    match = re.search(r'playlist/([A-Za-z0-9]+)', url_or_id)
    return match.group(1) if match else url_or_id.strip()

def get_playlist_for_analysis(sp, playlist_id):
    pl = sp.playlist(playlist_id, fields="id,name,description,tracks.total,owner.id,owner.display_name")
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

def ai_config_error(provider):
    if provider == "anthropic" and not ANTHROPIC_KEY:
        return "Falta ANTHROPIC_API_KEY en .env o selecciona otro proveedor en IA Config."
    if provider == "openai" and not OPENAI_KEY:
        return "Falta OPENAI_API_KEY en .env o selecciona otro proveedor en IA Config."
    if provider == "nvidia" and not NVIDIA_KEY:
        return "Falta NVIDIA_API_KEY en .env o selecciona otro proveedor en IA Config."
    if provider not in PROVIDERS:
        return f"Proveedor de IA no soportado: {provider}"
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

def spotify_search(sp, query, limit=10):
    limit = max(1, min(int(limit), 20))
    cache_key = (query, limit)
    if cache_key not in SPOTIFY_SEARCH_CACHE:
        SPOTIFY_SEARCH_CACHE[cache_key] = sp.search(q=query, type="track", limit=limit)["tracks"]["items"]
    return SPOTIFY_SEARCH_CACHE[cache_key]

def candidate_label(track):
    artist = track["artists"][0]["name"] if track.get("artists") else "Artista desconocido"
    return f"{artist} – {track['name']}"

def prompt_needs_instrumental_electronic_filter(mood):
    text = normalize_search_text(mood)
    wants_instrumental = any(term in text for term in [
        "instrumental", "sin voces", "sin voz", "no vocal", "without vocals",
        "programar", "enfoque", "concentracion", "focus", "trabajo", "estudiar"
    ])
    electronic_context = any(term in text for term in [
        "techno", "house", "electronic", "electronica", "electronico",
        "deep", "melodic", "minimal", "progressive", "microhouse"
    ])
    return wants_instrumental and electronic_context

def artist_genres(sp, artist_id):
    if not artist_id:
        return []
    if artist_id not in SPOTIFY_ARTIST_CACHE:
        SPOTIFY_ARTIST_CACHE[artist_id] = sp.artist(artist_id).get("genres", [])
    return SPOTIFY_ARTIST_CACHE[artist_id]

def track_allowed_by_prompt(sp, track, mood):
    if not prompt_needs_instrumental_electronic_filter(mood):
        return True

    label = normalize_search_text(candidate_label(track))
    rejected_terms = [
        "feat", "featuring", "ft", "vocal", "unplugged", "acoustic", "radio edit",
        "plena", "salsa", "bachata", "merengue", "reggaeton", "cumbia", "vallenato",
        "latin pop", "rock", "pop", "rap", "hip hop", "trap", "corridos", "banda",
        "mango", "ron",
    ]
    # Solo filtramos por el texto del track (sin llamar a sp.artist por género,
    # que disparaba muchísimas peticiones extra a Spotify y causaba rate-limit).
    if any(term in label for term in rejected_terms):
        return False
    return True

def find_spotify_track(sp, name, artist=None, exclude_ids=None, mood=None):
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
    for track in spotify_search(sp, f'artist:"{artist}"', limit=20):
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

def call_ai(prompt, provider="anthropic", model=None):
    """Llama al proveedor de IA seleccionado y devuelve el texto de respuesta."""

    # ── Anthropic ──
    if provider == "anthropic":
        model = model or "claude-sonnet-4-6"
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": model, "max_tokens": 1500,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=60
        )
        data = r.json()
        if not r.ok:
            return f"Error Anthropic: {data.get('error', {}).get('message', r.text)}"
        return data["content"][0]["text"]

    # ── OpenAI ──
    elif provider == "openai":
        model = model or "gpt-5.4-mini"
        headers = {"Authorization": f"Bearer {OPENAI_KEY}", "content-type": "application/json"}
        if model.startswith("gpt-5"):
            r = requests.post(
                "https://api.openai.com/v1/responses",
                headers=headers,
                json={"model": model, "max_output_tokens": 5000, "input": prompt},
                timeout=60
            )
        else:
            r = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json={"model": model, "max_tokens": 5000,
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
            json={"model": model, "max_tokens": 1500,
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
    return render_template("index.html", logged_in=logged_in, auth_error=request.args.get("auth_error"))

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
    ai_error = ai_config_error(provider)
    if ai_error:
        return jsonify({"error": ai_error}), 400
    ai_raw = call_ai(prompt, provider=provider, model=model)
    try:
        ai_raw_clean = ai_raw.strip().replace("```json","").replace("```","").strip()
        ai = json.loads(ai_raw_clean)
    except:
        ai = {"summary": ai_raw, "remove": [], "add": []}

    # Match remove suggestions with actual track IDs from playlist
    for r_item in ai.get("remove", []):
        for t in track_list:
            if r_item["name"].lower() in t["name"].lower() and r_item["artist"].lower() in t["artist"].lower():
                r_item["id"] = t["id"]
                r_item["uri"] = t["uri"]
                break

    return jsonify({
        "playlist_id": playlist_id,
        "playlist_name": pl["name"],
        "playlist_description": pl.get("description", ""),
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
        track = find_spotify_track(sp, item.get("name"), item.get("artist"), selected_ids)
        if track:
            selected_ids.add(track["id"])
            track_ids_to_add.append(track["id"])
            results["added"].append(candidate_label(track))
        else:
            results["not_found"].append(f"{item['artist']} – {item['name']}")

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


def stream_resolve_from_prompt(sp, name, mood, count, candidate_count, provider, model, deadline, preselected_ids, result):
    """Generador que pide canciones a la IA y las resuelve en Spotify, emitiendo
    eventos NDJSON de status/progress. Llena `result` (dict con listas). En caso de
    error terminal escribe result["fatal"]["error"] y termina; el llamador decide
    qué hacer. `preselected_ids` son IDs que NO se deben volver a agregar."""
    track_ids = result["track_ids"]
    added = result["added"]
    resolved_tracks = result["resolved_tracks"]
    not_found = result["not_found"]
    rejected = result["rejected"]
    replacement_added = result["replacement_added"]
    substitutes = result["substitutes"]
    fatal = result["fatal"]
    selected_ids = set(preselected_ids)  # incluye lo ya existente, para no duplicar

    def resolve_items(items, mark_replacement=False):
        """Busca las sugerencias en Spotify EN PARALELO y emite progreso en orden."""
        items = [it for it in items if it]
        if not items or len(track_ids) >= count or fatal:
            return
        exclude_snapshot = set(selected_ids)

        def resolve_one(it):
            # 1) intento exacto (título + artista). 2) si la IA inventó el título,
            # respaldo con una canción real del mismo artista.
            track = find_spotify_track(sp, it.get("name"), it.get("artist"), exclude_snapshot, mood)
            if track:
                return (track, False)
            if it.get("artist"):
                fb = find_artist_fallback(sp, it.get("artist"), exclude_snapshot, mood)
                if fb:
                    return (fb, True)
            return (None, False)

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(resolve_one, it) for it in items]
            for it, fut in zip(items, futures):
                if len(track_ids) >= count or time.monotonic() > deadline:
                    for f in futures:
                        f.cancel()
                    break
                try:
                    track, is_fallback = fut.result()
                except SpotifyException as e:
                    if getattr(e, "http_status", None) == 429:
                        fatal["error"] = ("Spotify limitó las peticiones (429) por demasiadas "
                                          "búsquedas seguidas. Espera unos minutos e intenta de nuevo.")
                        for f in futures:
                            f.cancel()
                        break
                    track, is_fallback = None, False
                except Exception:
                    track, is_fallback = None, False
                if track and track["id"] not in selected_ids:
                    selected_ids.add(track["id"])
                    track_ids.append(track["id"])
                    label = candidate_label(track)
                    added.append(label)
                    resolved_tracks.append({"id": track["id"], "label": label})
                    if mark_replacement:
                        replacement_added.append(label)
                    if is_fallback:
                        substitutes.append(label)
                    yield _event({"type": "progress", "done": len(track_ids), "total": count, "label": label})
                elif not track:
                    not_found.append(f"{it.get('artist', 'Artista desconocido')} – {it.get('name', 'Canción desconocida')}")

    yield _event({"type": "status", "message": "La IA está eligiendo las canciones…"})
    ai_raw = call_ai(_create_prompt(name, mood, count, candidate_count), provider=provider, model=model)
    try:
        ai = json.loads(ai_raw.strip().replace("```json", "").replace("```", "").strip())
    except Exception:
        fatal["error"] = f"La IA no devolvió una lista válida. Respuesta: {ai_raw[:200]}"
        return

    result["description"] = ai.get("description", mood)
    yield _event({"type": "status", "message": "Verificando canciones en Spotify…"})
    yield from resolve_items(ai.get("tracks", []))
    if fatal:
        return

    # Reintento: pedir reemplazos si faltan canciones.
    if len(track_ids) < count and time.monotonic() < deadline:
        needed = count - len(track_ids)
        yield _event({"type": "status", "message": f"Buscando {needed} canción(es) más con la IA…"})
        retry_raw = call_ai(_retry_prompt(name, mood, needed, added, not_found), provider=provider, model=model)
        try:
            retry_ai = json.loads(retry_raw.strip().replace("```json", "").replace("```", "").strip())
        except Exception:
            retry_ai = {"tracks": []}
        yield from resolve_items(retry_ai.get("tracks", []), mark_replacement=True)
        if fatal:
            return

    # Rondas de reparación adicionales.
    repair_round = 0
    max_repair_rounds = 1 if count > 30 else 2
    while len(track_ids) < count and repair_round < max_repair_rounds and time.monotonic() < deadline:
        repair_round += 1
        needed = count - len(track_ids)
        yield _event({"type": "status", "message": f"Completando la lista ({len(track_ids)}/{count})…"})
        repair_raw = call_ai(_repair_prompt(name, mood, needed, added, rejected, not_found), provider=provider, model=model)
        try:
            repair_ai = json.loads(repair_raw.strip().replace("```json", "").replace("```", "").strip())
        except Exception:
            repair_ai = {"tracks": []}
        before_count = len(track_ids)
        yield from resolve_items(repair_ai.get("tracks", []), mark_replacement=True)
        if fatal:
            return
        if len(track_ids) == before_count:
            break


def _new_result(mood):
    return {
        "track_ids": [], "added": [], "resolved_tracks": [], "not_found": [],
        "rejected": [], "replacement_added": [], "substitutes": [],
        "fatal": {}, "description": mood,
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
    count = max(5, min(int(data.get("count", 15)), 50))
    candidate_count = min(count + 6, 55)
    deadline = time.monotonic() + 220
    provider = data.get("provider", "anthropic")
    model    = data.get("model", None)
    ai_error = ai_config_error(provider)
    if ai_error:
        return jsonify({"error": ai_error}), 400

    def generate():
        result = _new_result(mood)
        try:
            yield from stream_resolve_from_prompt(sp, name, mood, count, candidate_count, provider, model, deadline, set(), result)
            if result["fatal"]:
                yield _event({"type": "error", "error": result["fatal"]["error"]})
                return
            if not result["track_ids"]:
                yield _event({"type": "error", "error": "No se encontró ninguna canción real en Spotify para crear la playlist. Prueba un mood más concreto o menos restrictivo."})
                return

            yield _event({"type": "status", "message": "Creando la playlist en tu Spotify…"})
            try:
                pl = sp.current_user_playlist_create(name=name, public=False, description=result["description"])
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
                "replacement_added": result["replacement_added"],
                "substitutes": result["substitutes"],
                "not_found": result["not_found"],
                "rejected": result["rejected"],
                "target_count": count,
                "timed_out": len(result["track_ids"]) < count and time.monotonic() >= deadline,
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
    count = max(1, min(int(data.get("count", 10)), 50))
    candidate_count = min(count + 6, 55)
    deadline = time.monotonic() + 220
    provider = data.get("provider", "anthropic")
    model    = data.get("model", None)
    ai_error = ai_config_error(provider)
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

            yield from stream_resolve_from_prompt(sp, playlist_name, mood, count, candidate_count, provider, model, deadline, existing_ids, result)
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
                "replacement_added": result["replacement_added"],
                "substitutes": result["substitutes"],
                "not_found": result["not_found"],
                "rejected": result["rejected"],
                "target_count": count,
                "timed_out": len(result["track_ids"]) < count and time.monotonic() >= deadline,
            })
        except Exception as e:
            app.logger.exception("Error en /api/add_to_playlist stream")
            yield _event({"type": "error", "error": f"Error interno: {str(e)}"})

    return _ndjson_response(generate())


def _create_prompt(name, mood, count, candidate_count):
    return f"""Eres un experto DJ y curador musical. El usuario quiere crear una playlist con este concepto:

Título: "{name}"
Descripción/Mood: "{mood}"
Cantidad final deseada: {count}

Genera {candidate_count} canciones candidatas para que el sistema pueda verificar disponibilidad en Spotify y quedarse con las mejores {count}. Deben ser canciones reales, oficiales y disponibles en Spotify.
Respeta literalmente restricciones del usuario como instrumental, sin voces, género, BPM, mood, época, idioma, país, energía o artistas de referencia.
Si el usuario menciona programar, enfoque, concentración, relajante, estudiar, deep, smooth, mentalidad o manifestación, prioriza canciones hipnóticas, limpias, repetitivas y de energía baja/media; evita tracks agresivos, ruidosos, industriales, peak-time, rave, acid o demasiado intensos aunque pertenezcan al género pedido.
Evita inventar remixes, bootlegs, edits, club mixes o títulos raros si no estás seguro de que existen oficialmente.
Prefiere tracks fáciles de encontrar por búsqueda de Spotify. Varía los artistas — no repitas más de 2 canciones del mismo artista.

Responde SOLO en este formato JSON (sin markdown, sin texto extra):
{{
  "description": "Descripción corta de la playlist (1 oración)",
  "tracks": [
    {{"name": "Nombre exacto de la canción", "artist": "Artista exacto"}}
  ]
}}"""


def _retry_prompt(name, mood, needed, added, not_found):
    return f"""El usuario pidió esta playlist:

Título: "{name}"
Descripción/Mood: "{mood}"

Ya encontré estas canciones reales en Spotify:
{json.dumps(added, ensure_ascii=False)}

Estas sugerencias NO se encontraron en Spotify o no fueron utilizables:
{json.dumps(not_found, ensure_ascii=False)}

Necesito exactamente {needed} reemplazos adicionales. Respeta estrictamente el prompt original del usuario y evita repetir artistas/canciones ya listadas. Si el prompt habla de programar/enfoque/relajante, los reemplazos deben ser suaves, hipnóticos, limpios y poco distractores, no peak-time ni ruidosos.
Devuelve solo JSON en este formato:
{{
  "tracks": [
    {{"name": "Nombre exacto en Spotify", "artist": "Artista exacto"}}
  ]
}}"""


def _repair_prompt(name, mood, needed, added, rejected, not_found):
    return f"""El intento anterior todavía no alcanzó la cantidad solicitada.

Prompt original del usuario:
Título: "{name}"
Descripción/Mood: "{mood}"

Canciones aceptadas hasta ahora:
{json.dumps(added, ensure_ascii=False)}

Canciones rechazadas por no cumplir el prompt:
{json.dumps(rejected, ensure_ascii=False)}

Canciones que no se encontraron en Spotify:
{json.dumps(not_found, ensure_ascii=False)}

Necesito {needed} canciones adicionales reales de Spotify. Respeta literalmente el prompt original. No propongas canciones ya aceptadas, rechazadas o no encontradas. Si el prompt habla de programar/enfoque/relajante, evita canciones ruidosas, agresivas o demasiado intensas aunque sean del género correcto.
Devuelve SOLO JSON:
{{
  "tracks": [
    {{"name": "Nombre exacto en Spotify", "artist": "Artista exacto"}}
  ]
}}"""


@app.route("/api/providers")
def api_providers():
    available = {}
    for key, prov in PROVIDERS.items():
        has_key = bool(
            (key == "anthropic" and ANTHROPIC_KEY) or
            (key == "openai"    and OPENAI_KEY)    or
            (key == "nvidia"    and NVIDIA_KEY)
        )
        available[key] = {**prov, "available": has_key}
    return jsonify(available)

if __name__ == "__main__":
    app.run(debug=False, use_reloader=False, port=5000)



