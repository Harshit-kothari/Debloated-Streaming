#!/usr/bin/env python3
"""
Debloated Streaming — Hotstar Relay
-----------------------
Generates shareable links for Hotstar live streams.
Family/friends open the link in any browser — stream plays via hls.js.
The local service relays playlists and media requests for your player.

pip install flask requests pyngrok
python hotstar_live.py
open http://localhost:8080

A public tunnel starts only when explicitly enabled.
Set NGROK_AUTHTOKEN env var or paste token in Settings on the page.

Original relay by Rvind (arvind88765); extended for Debloated Streaming.
"""

import os, re, json, time, hmac, hashlib, threading, uuid
from urllib.parse import urlparse, urljoin

# Dependencies are installed explicitly; importing the backend never runs pip.
try:
    from flask import Flask, request, Response, jsonify
    import requests as req
except ImportError as exc:
    raise ImportError("Missing Python dependencies. Run: python -m pip install -r Requirements.txt") from exc

# curl_cffi for CDN (live09p.hotstar.com = Akamai with TLS fingerprint check)
_cdn_session = None
def _get_cdn_session():
    global _cdn_session
    if _cdn_session is not None:
        return _cdn_session
    try:
        from curl_cffi import requests as cffi_req
        supported = []
        try:
            from curl_cffi.requests import BrowserType
            supported = [b.value for b in BrowserType]
        except Exception:
            pass
        preferred = ["chrome131","chrome130","chrome124","chrome120","chrome116","chrome110","chrome107","chrome104","chrome101","chrome100","chrome99"]
        target = next((t for t in preferred if t in supported), None)
        if not target and supported:
            target = next((t for t in supported if "chrome" in t), supported[0])
        if not target:
            target = "chrome110"
        s = cffi_req.Session(impersonate=target)
        _cdn_session = (s, target)
        print(f"[init] curl_cffi CDN session ready: impersonate={target}")
        return _cdn_session
    except ImportError:
        pass
    print("[init] curl_cffi not installed — CDN fetch uses plain requests")
    _cdn_session = (None, None)
    return None, None

# ---------------------------------------------------------------------------
# CONSTANTS  (from HAR analysis)
# ---------------------------------------------------------------------------

_HMAC_KEY = b"\x05\xfc\x1a\x01\xca\xc9\x4b\xc4\x12\xfc\x53\x12\x07\x75\xf9\xee"

# Exact client string seen in HAR for BFF calls
_CLIENT_WEB = (
    "platform:web;app_version:26.09.05.0;browser:Chrome;"
    "schema_version:0.0.1797;os:Windows;os_version:10;"
    "browser_version:137;network_data:4g"
)

# Exact client_capabilities from HAR (decoded from URL param)
_CLIENT_CAPS = json.dumps({
    "ads":                  ["non_ssai"],
    "audio_channel":        ["stereo"],
    "container":            ["fmp4", "fmp4br", "ts"],
    "dvr":                  ["short"],          # required for live HLS
    "dynamic_range":        ["sdr"],
    "encryption":           ["widevine", "plain"],
    "ladder":               ["web", "tv", "phone"],
    "package":              ["dash", "hls"],
    "resolution":           ["sd", "hd", "fhd"],
    "video_codec":          ["h264"],
    "video_codec_non_secure": ["h264"],
}, separators=(",", ":"))

_DRM_PARAMS = json.dumps({
    "hdcp_version":              ["HDCP_V2_2"],
    "widevine_security_level":   ["HW_SECURE_ALL", "HW_SECURE_CRYPTO", "SW_SECURE_DECODE"],
    "playready_security_level":  [],
}, separators=(",", ":"))

APP_DIR    = os.path.dirname(os.path.abspath(__file__))
PORT       = int(os.environ.get("HOTSTAR_PORT", "8080"))
_CHROME_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"

# ---------------------------------------------------------------------------
# NGROK TUNNEL
# ---------------------------------------------------------------------------

_ngrok_url   = None
_ngrok_lock  = threading.Lock()

def _ngrok_token_path():
    return os.path.join(APP_DIR, "ngrok_token.txt")

def _load_ngrok_token():
    """Read token from env or ngrok_token.txt file."""
    t = os.environ.get("NGROK_AUTHTOKEN", "").strip()
    if t:
        return t
    p = _ngrok_token_path()
    if os.path.exists(p):
        t = open(p).read().strip()
        if t:
            return t
    return None

def _save_ngrok_token(token):
    with open(_ngrok_token_path(), "w") as f:
        f.write(token.strip())

def start_ngrok(port=PORT):
    """
    Attempt to start an ngrok tunnel.
    Returns public URL string or None if pyngrok not installed / no token.
    """
    global _ngrok_url
    try:
        from pyngrok import ngrok, conf, exception as ngrok_exc
    except ImportError:
        print("[ngrok] pyngrok not installed. run: pip install pyngrok")
        return None

    token = _load_ngrok_token()
    if not token:
        print("[ngrok] no auth token — skipping tunnel. set NGROK_AUTHTOKEN or paste in the web UI.")
        return None

    try:
        conf.get_default().auth_token = token
        tunnel = ngrok.connect(port, "http")
        url    = tunnel.public_url
        # ngrok gives http:// — upgrade to https:// (ngrok always supports it)
        if url.startswith("http://"):
            url = "https://" + url[7:]
        with _ngrok_lock:
            _ngrok_url = url
        print(f"[ngrok] tunnel UP → {url}")
        return url
    except Exception as e:
        print(f"[ngrok] failed to start tunnel: {e}")
        return None

def stop_ngrok():
    try:
        from pyngrok import ngrok
        ngrok.kill()
        print("[ngrok] tunnel closed")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# AUTH
# ---------------------------------------------------------------------------

import hotstar_auth as auth
load_token = auth.load_token
jwt_exp = auth.jwt_exp
tok_valid = auth.tok_valid
tok_remaining = auth.tok_remaining
save_token_file = auth.save_token_file
get_device_id_from_token = auth.get_device_id_from_token
api_guest_token = auth.api_guest_token
api_send_otp = auth.api_send_otp
api_verify_otp = auth.api_verify_otp

_FP_SAMPLE = (
    'MDA3MGYyZTAtZGYxMS00ODhjLThlNmItYjczYmEwNjNhYjIz.BPicufgJ44ZZHXqTv8pNoJXgX2WYShUBEW8vws'
    '__IjBu1VBsW5t32-Q0A8EFjVP0Wl7fvAEIDtfDMTqUQHJ5bNIRd9uO6-6mviC7-Axb9ZSwD3VCAclSrxGotyIgc'
    'axpXZSC9w6rijZGqbp8Hr3FkiHZTG6fqCdlVifI0ONKxowYqWKwfL9PqzngxBW4IGLm6k__sMgoPTDTEdUJDM3A'
    'gsFd2RIdw4WpU8ydA1OnXiyjlrqIJvNQ0riuqrLILC4UQ4j3oU_-yNwQPO1NRChLMCiQzLsG8Gr35oMPhcxoKur'
    '0Rv3M7oJR-PaFVrtwZhnreWtZ3Yyj5ySkkhFFh7qHENQRRj-paiWnaNny4BLhlcWPji1Lb6sZLTdjQAEvXTL38K'
    'MiFBcgxkaVgRAFhCiuTVqx4LPQ3oicviTI5LdocPAfGHunCSPwi-nnML_hEAXRlw3GXGZcsmujLeMgrJVwyn05y'
)

def _make_hotstar_auth():
    st = int(time.time()); exp = st + 6000
    msg = f"st={st}~exp={exp}~acl=/*"
    return f"{msg}~hmac={hmac.new(_HMAC_KEY, msg.encode(), hashlib.sha256).hexdigest()}"

def _dl_android_hdrs(token=None):
    """Android headers for login flow (matches jiohotstar-downloader exactly)."""
    h = {
        "User-Agent":          "Hotstar;in.startv.hotstar/26.09.05.0.11013 (Android/14)",
        "Content-Type":        "application/x-protobuf",
        "X-Country-Code":      "in", "X-HS-App": "11013",
        "X-HS-APP-ID":         "c86aad81-d602-46e5-b6a0-6d3891199063",
        "X-HS-Client":         "platform:android;app_id:in.startv.hotstar;app_version:26.09.05.0;os:Android;os_version:14;schema_version:0.0.1797;brand:Samsung;model:SM-S918B;carrier:airtel;network_data:NETWORK_TYPE_WIFI",
        "X-HS-Device-Id":      _DEVICE_ID, "X-HS-Platform": "android",
        "X-HS-Schema-Version": "0.0.1797",
        "X-HS-FP-Info":        _FP_SAMPLE,
        "hotstarauth":         _make_hotstar_auth(),
    }
    if token: h["X-HS-Usertoken"] = token
    return h

def _dl_web_hdrs(token=None, ps=None):
    """Web headers for login flow (matches jiohotstar-downloader exactly)."""
    h = {
        "User-Agent":    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
        "Accept":        "application/json, text/plain, */*",
        "Content-Type":  "application/json",
        "Origin":        "https://www.hotstar.com",
        "Referer":       "https://www.hotstar.com/in",
        "x-country-code":"in", "x-hs-app": "260905000",
        "x-hs-client":   "platform:web;app_version:26.09.05.0;browser:Chrome;schema_version:0.0.1797;os:Windows;os_version:10;browser_version:137;network_data:4g",
        "x-hs-platform": "web", "hotstarauth": _make_hotstar_auth(),
    }
    if token: h["x-hs-usertoken"] = token
    if ps:    h["x-hs-proxystate"] = ps
    return h

# ---------------------------------------------------------------------------
# PROXYSTATE
# HAR shows the browser sends a pre-stored proxystate value on every BFF
# request — there is no live /bff/v2/start call before each fetch.
# We load it from hotstar_proxystate.json (saved from a browser HAR capture)
# and refresh it when stale.
# ---------------------------------------------------------------------------

_proxystate     = {"value": None, "ud": None, "fetched_at": 0}
_proxystate_lock = threading.Lock()

def load_proxystate_from_file():
    """
    Load proxystate from hotstar_proxystate.json.
    The file should contain the x-hs-proxystate and x-hs-proxystate-ud
    header values extracted from a browser HAR capture.
    """
    p = os.path.join(APP_DIR, "hotstar_proxystate.json")
    if not os.path.exists(p):
        return False
    try:
        d = json.load(open(p))
        ps   = d.get("proxystate", "")
        ps_ud= d.get("proxystate_ud", "")
        if ps:
            with _proxystate_lock:
                _proxystate.update({"value": ps, "ud": ps_ud, "fetched_at": time.time()})
            print(f"[auth] proxystate loaded from file (len={len(ps)})")
            return True
    except Exception as e:
        print(f"[auth] proxystate file error: {e}")
    return False

def _make_start_body():
    return {
        "deeplink_url": "",
        "context": {
            "url":   "type.googleapis.com/context.StateContext",
            "value": "CgQaAggC",
        },
        "app_launch_count": 1,
    }

def _make_start_headers(token, referer):
    hdrs = {
        "accept":             "application/json, text/plain, */*",
        "accept-encoding":    "gzip, deflate",
        "accept-language":    "en-US,en;q=0.9",
        "cache-control":      "no-cache",
        "content-type":       "application/json",
        "origin":             "https://www.hotstar.com",
        "pragma":             "no-cache",
        "priority":           "u=1, i",
        "referer":            referer,
        "sec-ch-ua":          '"Google Chrome";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
        "sec-ch-ua-mobile":   "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest":     "empty",
        "sec-fetch-mode":     "cors",
        "sec-fetch-site":     "same-origin",
        "user-agent":         _CHROME_UA,
        "x-country-code":     "in",
        "x-hs-app":           "260905000",
        "x-hs-client":        _CLIENT_WEB,
        "x-hs-device-id":     _DEVICE_ID,
        "x-hs-platform":      "web",
        "x-request-id":       str(uuid.uuid4()),
    }
    if token:
        hdrs["x-hs-usertoken"] = token
    return hdrs

def _try_start_with(poster, token, referer, label):
    """Call /bff/v2/start with the given HTTP poster (requests or curl_cffi session)."""
    try:
        hdrs = _make_start_headers(token, referer)
        body = _make_start_body()
        r = poster.post("https://www.hotstar.com/api/internal/bff/v2/start",
                        headers=hdrs, json=body, timeout=12)
        print(f"[auth] /start [{label}] status={r.status_code} resp-len={len(r.content)}")
        ps_keys = [k for k in r.headers if "proxystate" in k.lower() or "proxy" in k.lower()]
        print(f"[auth] /start proxy header names: {ps_keys}")
        ps = r.headers.get("x-hs-setproxystate") or r.headers.get("x-hs-proxystate")
        ud = r.headers.get("x-hs-setproxystate-ud") or r.headers.get("x-hs-proxystate-ud")
        if ps:
            print(f"[auth] proxystate refreshed via [{label}] (len={len(ps)})")
            with _proxystate_lock:
                _proxystate.update({"value": ps, "ud": ud, "fetched_at": time.time()})
            return ps, ud
        else:
            print(f"[auth] /start [{label}] — no proxystate in response headers")
            try:
                print("[auth] /start returned no session state")
            except Exception:
                pass
    except Exception as e:
        print(f"[auth] /start [{label}] exception: {e}")
    return None, None

def get_proxystate(token, referer="https://www.hotstar.com/in"):
    """
    Get proxystate via fallback chain:
    1. Use cached value if fresh (< 55 min)
    2. Try curl_cffi (Chrome TLS fingerprint) — Hotstar now checks TLS on BFF too
    3. Try plain requests as last resort
    Returns (proxystate_value, proxystate_ud) or (None, None)
    """
    with _proxystate_lock:
        age = time.time() - _proxystate["fetched_at"]
        if _proxystate["value"] and age < 3300:
            print(f"[auth] using cached proxystate (age={int(age)}s)")
            return _proxystate["value"], _proxystate["ud"]

    print(f"[auth] fetching fresh proxystate...")

    # curl_cffi first — Chrome TLS fingerprint bypasses Akamai bot detection on BFF
    cdn_s, impersonate_target = _get_cdn_session()
    if cdn_s is not None:
        ps, ud = _try_start_with(cdn_s, token, referer, f"curl_cffi/{impersonate_target}")
        if ps:
            return ps, ud
        print("[auth] curl_cffi /start failed — trying plain requests")

    ps, ud = _try_start_with(req, token, referer, "requests")
    if ps:
        return ps, ud

    print("[auth] WARNING: could not get fresh proxystate — using stale/file value if any")
    with _proxystate_lock:
        return _proxystate["value"], _proxystate["ud"]

# ---------------------------------------------------------------------------
# SLUG EXTRACTION  (from Hotstar URL)
# ---------------------------------------------------------------------------

def extract_slug(hotstar_url):
    """
    Extract the BFF slug from a Hotstar URL.

    HAR-confirmed endpoint is /live/watch (not /video/live/watch).
    From https://www.hotstar.com/in/shows/bbs10-24x7-stream-deferred/1271698292/live
    extracts in/shows/bbs10-24x7-stream-deferred/1271698292

    Returns (slug, content_id) or (None, None).
    """
    if not isinstance(hotstar_url, str):
        return None, None
    try:
        parsed = urlparse(hotstar_url.strip() if "://" in hotstar_url else "https://" + hotstar_url.strip())
        valid_host = parsed.hostname in ("www.hotstar.com", "hotstar.com") and parsed.port in (None, 443)
    except ValueError:
        return None, None
    if parsed.scheme != "https" or not valid_host or parsed.username:
        return None, None
    path = parsed.path.strip("/")
    for _ in range(5):
        path = re.sub(r'/(video/live/watch|live/watch|video/watch|video/live|live|watch)$', '', path)
    if not path.startswith("in/") or ".." in path or not re.fullmatch(r"[A-Za-z0-9_/-]+", path):
        return None, None
    ids = re.findall(r'/(\d{9,12})(?:/|$)', path)
    return (path, ids[-1]) if ids else (None, None)


# ---------------------------------------------------------------------------
# BFF SLUG API  (main live stream URL fetcher)
# ---------------------------------------------------------------------------

def _bff_headers(token, ps, ps_ud, slug, lang):
    # Full Chrome 137 header set — Akamai checks these for bot detection
    hdrs = {
        "accept":                    "application/json, text/plain, */*",
        "accept-encoding":           "gzip, deflate",  # no br/zstd — requests can't decode brotli natively
        "accept-language":           "eng",        # exact from HAR
        "cache-control":             "no-cache",
        "origin":                    "https://www.hotstar.com",
        "pragma":                    "no-cache",
        "priority":                  "u=1, i",
        "referer":                   "https://www.hotstar.com/in/home",  # exact from HAR
        "sec-ch-ua":                 '"Google Chrome";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
        "sec-ch-ua-mobile":          "?0",
        "sec-ch-ua-platform":        '"Windows"',
        "sec-fetch-dest":            "empty",
        "sec-fetch-mode":            "cors",
        "sec-fetch-site":            "same-origin",
        "user-agent":                _CHROME_UA,
        "x-country-code":            "in",
        "x-hs-accept-language":      lang,
        "x-hs-app":                  "260905000",
        "x-hs-client":               _CLIENT_WEB,
        "x-hs-client-targeting":     f"ad_id:{_DEVICE_ID};user_lat:false;",  # from HAR
        "x-hs-device-id":            _DEVICE_ID,
        "x-hs-is-retry":             "false",
        "x-hs-platform":             "web",
        "x-hs-retry-count":          "0",
        "x-request-id":              str(uuid.uuid4()),
    }
    if token:  hdrs["x-hs-usertoken"]     = token
    if ps:     hdrs["x-hs-proxystate"]    = ps
    if ps_ud:  hdrs["x-hs-proxystate-ud"] = ps_ud
    return hdrs

def _extract_m3u8(d, label=""):
    """Walk multiple known JSON paths + regex scan to find an m3u8 URL."""
    # path 1: sports live events
    try:
        player = d["success"]["page"]["spaces"]["player"]
        ww     = player["widget_wrappers"][0]["widget"]["data"]["player_config"]
        for asset_key in ("media_asset", "media_asset_v2"):
            asset = ww.get(asset_key, {})
            for url_key in ("content_url", "primary"):
                val = asset.get(url_key) if isinstance(asset.get(url_key), str) else \
                      (asset.get(url_key) or {}).get("content_url")
                if val and ".m3u8" in val:
                    print(f"[bff] m3u8 via path1/{asset_key}/{url_key} {label}")
                    return val
            urls_list = asset.get("content_urls", [])
            if urls_list and isinstance(urls_list[0], str):
                print(f"[bff] m3u8 via path1/{asset_key}/content_urls {label}")
                return urls_list[0]
    except (KeyError, IndexError, TypeError):
        pass

    # path 2: content > playbackSets
    try:
        psets = d["success"]["page"]["content"]["playbackSets"]
        for ps in psets:
            url = ps.get("playbackUrl") or ps.get("manifestUrl") or ps.get("contentUrl")
            if url and ".m3u8" in url:
                print(f"[bff] m3u8 via playbackSets {label}")
                return url
    except (KeyError, TypeError):
        pass

    # path 3: regex scan entire body — catches schema variations
    raw  = json.dumps(d)
    hits = re.findall(r'https://[^"\\]+\.m3u8[^"\\]*', raw)
    if hits:
        print(f"[bff] m3u8 via regex scan {label}")
        return hits[0]

    return None

def _do_bff_get(url, hdrs, params, label):
    """
    Try BFF GET first with curl_cffi (Chrome TLS), fall back to plain requests.
    Returns (response, used_curl_cffi) or (None, False).
    """
    cdn_s, target = _get_cdn_session()
    if cdn_s is not None:
        try:
            r = cdn_s.get(url, headers=hdrs, params=params, timeout=15)
            print(f"[bff] [{label}] curl_cffi/{target} status={r.status_code} len={len(r.content)}")
            return r, True
        except Exception as e:
            print(f"[bff] [{label}] curl_cffi error: {e}")
    try:
        r = req.get(url, headers=hdrs, params=params, timeout=15)
        print(f"[bff] [{label}] requests status={r.status_code} len={len(r.content)}")
        return r, False
    except Exception as e:
        print(f"[bff] [{label}] requests error: {e}")
    return None, False

def fetch_live_urls(slug, token, lang="eng"):
    """
    Try multiple BFF endpoint variants — sports uses /video/live/watch,
    shows/channels may use /video/watch or /watch.
    Returns an m3u8 URL string or None.
    """
    ps, ps_ud = get_proxystate(token, referer=f"https://www.hotstar.com/{slug}")
    if not ps:
        print("[bff] WARNING: no proxystate — BFF will likely return HTML")
    else:
        print(f"[bff] using proxystate (len={len(ps)})")

    hdrs   = _bff_headers(token, ps, ps_ud, slug, lang)
    params = {
        "client_capabilities": _CLIENT_CAPS,
        "drm_parameters":      _DRM_PARAMS,
        "request_features":    "consent_supported",
        "lang":                lang,
    }

    base = f"https://www.hotstar.com/api/internal/bff/v2/slugs/{slug}"
    endpoints = [
        f"{base}/live/watch",         # HAR confirmed for shows/channels (BB 24x7)
        f"{base}/video/live/watch",   # sports live events
        f"{base}/watch",              # fallback
    ]

    for ep in endpoints:
        ep_label = ep.split("/bff/v2/slugs/")[-1]
        print(f"[bff] trying {ep_label} lang={lang}")
        try:
            r, used_cffi = _do_bff_get(ep, hdrs, params, ep_label)
            if r is None:
                continue

            if r.status_code == 404:
                print("[bff] content endpoint not found")
                continue
            if r.status_code != 200:
                print(f"[bff] upstream HTTP {r.status_code}")
                continue

            # A HTML response means proxystate is missing or expired
            if r.text.lstrip().startswith("<!DOCTYPE") or r.text.lstrip().startswith("<html"):
                print(f"[bff] got HTML response ({len(r.content)}b) — proxystate missing/expired, forcing refresh")
                with _proxystate_lock:
                    _proxystate["fetched_at"] = 0
                ps, ps_ud = get_proxystate(token, referer=f"https://www.hotstar.com/{slug}")
                if ps:
                    hdrs = _bff_headers(token, ps, ps_ud, slug, lang)
                    r, used_cffi = _do_bff_get(ep, hdrs, params, ep_label + "_retry")
                    if r is None or r.status_code != 200:
                        continue
                    if r.text.lstrip().startswith("<"):
                        print(f"[bff] still HTML after proxystate refresh — Hotstar blocking")
                        continue
                else:
                    continue

            try:
                d = r.json()
            except Exception as je:
                print("[bff] provider returned an invalid JSON response")
                continue
            m3u8 = _extract_m3u8(d, label=ep_label)
            if m3u8:
                return m3u8

            print("[bff] no compatible HLS stream in provider response")

        except Exception as e:
            import traceback
            print(f"[bff] exception: {e}")
            traceback.print_exc()

    return None

# ---------------------------------------------------------------------------
# SESSION STORE  (active streams, keyed by share_id)
# ---------------------------------------------------------------------------

_TOKEN     = load_token()
_DEVICE_ID = get_device_id_from_token(_TOKEN) if _TOKEN else str(uuid.uuid4())[:23]
load_proxystate_from_file()
_sessions  = {}   # share_id -> {slug, m3u8_url, base_url, created_at, title}
_sess_lock = threading.Lock()

def make_share_id():
    return uuid.uuid4().hex[:10]

# ---------------------------------------------------------------------------
# FLASK APP
# ---------------------------------------------------------------------------

app = Flask(__name__)

@app.route("/")
def index():
    return HOST_HTML

@app.route("/watch/<share_id>")
def watch(share_id):
    with _sess_lock:
        sess = _sessions.get(share_id)
    if not sess:
        return "link expired or invalid", 404
    from html import escape
    return WATCH_HTML.replace("__SHARE_ID__", share_id).replace("__TITLE__", escape(sess.get("title","Live Stream")))

def _current_token():
    global _TOKEN, _DEVICE_ID
    token = load_token()
    if token != _TOKEN:
        _TOKEN = token
        _DEVICE_ID = get_device_id_from_token(token) if token else str(uuid.uuid4())
        with _proxystate_lock:
            _proxystate.update({"value": None, "ud": None, "fetched_at": 0})
    return token


@app.route("/api/create", methods=["POST"])
def api_create():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "Expected a JSON object."}), 400
    slug, cid = extract_slug(data.get("url", ""))
    if not slug:
        return jsonify({"error": "Enter a valid https://www.hotstar.com/in/ content URL.", "code": "invalid_url"}), 400
    lang = data.get("lang", "eng")
    if not isinstance(lang, str) or not re.fullmatch(r"[a-z]{2,3}", lang):
        return jsonify({"error": "Choose a valid audio language.", "code": "invalid_language"}), 400
    token = _current_token()
    if not auth.authenticated_token(token):
        return jsonify({"error": "Log in to JioHotstar before opening a stream.", "code": "login_required"}), 401
    m3u8 = fetch_live_urls(slug, token, lang=lang)
    if not m3u8:
        return jsonify({"error": "JioHotstar did not provide a compatible HLS stream. Check the content is live and available to your account and region. DRM content requires the official player.", "code": "stream_unavailable"}), 502
    if urlparse(m3u8).scheme not in ("https", "http"):
        return jsonify({"error": "Provider returned an invalid stream URL."}), 502
    share_id = make_share_id()
    with _sess_lock:
        _sessions[share_id] = {
            "slug": slug, "m3u8_url": m3u8, "lang": lang,
            "created_at": time.time(), "resources": {}, "resource_ids": {},
            "title": slug.split("/")[-2].replace("-", " ").title(),
        }
    base = request.host_url.rstrip("/")
    with _ngrok_lock:
        pub = _ngrok_url
    return jsonify({
        "share_id": share_id, "watch_link": f"{base}/watch/{share_id}",
        "public_link": f"{pub}/watch/{share_id}" if pub else None,
        "m3u8_proxy": f"{base}/proxy/{share_id}/master.m3u8",
        "expires_in": "Provider URLs expire; refresh this session if playback stops.",
        "ngrok_active": pub is not None,
    })


@app.route("/api/refresh/<share_id>", methods=["POST"])
def api_refresh(share_id):
    with _sess_lock:
        sess = _sessions.get(share_id)
    if not sess:
        return jsonify({"error": "session not found"}), 404
    token = _current_token()
    if not auth.authenticated_token(token):
        return jsonify({"error": "Log in again before refreshing.", "code": "login_required"}), 401
    m3u8 = fetch_live_urls(sess["slug"], token, lang=sess["lang"])
    if not m3u8:
        return jsonify({"error": "Stream refresh failed; check availability in the official player."}), 502
    with _sess_lock:
        sess["m3u8_url"] = m3u8
        sess["refreshed"] = time.time()
    return jsonify({"status": "refreshed"})


def _cdn_headers():
    """
    Headers for live09p.hotstar.com CDN requests (from HAR).
    sec-fetch-site must be same-site (live09p shares the hotstar.com eTLD+1) —
    cross-site triggers Akamai bot detection immediately.
    """
    return {
        "accept":             "*/*",
        "accept-encoding":    "gzip, deflate, br, zstd",
        "accept-language":    "en-GB,en-IN;q=0.9,en-US;q=0.8,en;q=0.7,bn;q=0.6",
        "cache-control":      "no-cache",
        "origin":             "https://www.hotstar.com",
        "pragma":             "no-cache",
        "priority":           "u=1, i",
        "referer":            "https://www.hotstar.com/",
        "sec-ch-ua":          '"Google Chrome";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
        "sec-ch-ua-mobile":   "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest":     "empty",
        "sec-fetch-mode":     "cors",
        "sec-fetch-site":     "same-site",    # HAR confirmed — shares hotstar.com eTLD+1
        "user-agent":         _CHROME_UA,
    }

def _resource_url(share_id, upstream_url, playlist=False):
    """Register only URLs actually referenced by a provider playlist."""
    parsed = urlparse(upstream_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username:
        raise ValueError("Unsupported media URI in playlist")
    key = (upstream_url, playlist)
    with _sess_lock:
        sess = _sessions[share_id]
        resources = sess.setdefault("resources", {})
        ids = sess.setdefault("resource_ids", {})
        rid = ids.get(key)
        if rid is None:
            rid = uuid.uuid4().hex
            ids[key] = rid
            resources[rid] = key
    return f"/proxy/{share_id}/resource/{rid}"


def rewrite_playlist(body, base_url, share_id):
    """Resolve against the final playlist URL, retaining every host/path/query."""
    if not body.lstrip().startswith("#EXTM3U"):
        raise ValueError("Provider returned an invalid HLS playlist")
    lines = []
    variant = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            absolute = urljoin(base_url, stripped)
            is_playlist = variant or urlparse(absolute).path.lower().endswith(".m3u8")
            lines.append(_resource_url(share_id, absolute, is_playlist))
            variant = False
            continue
        if stripped.startswith("#EXT-X-STREAM-INF:"):
            variant = True
        if stripped.startswith("#"):
            playlist_tag = stripped.startswith(("#EXT-X-MEDIA:", "#EXT-X-I-FRAME-STREAM-INF:", "#EXT-X-RENDITION-REPORT:"))
            def replace_uri(match):
                uri = match.group(1)
                absolute = urljoin(base_url, uri)
                is_playlist = playlist_tag or urlparse(absolute).path.lower().endswith(".m3u8")
                return 'URI="' + _resource_url(share_id, absolute, is_playlist) + '"'
            line = re.sub(r'URI="([^"\r\n]+)"', replace_uri, line)
        lines.append(line)
    return "\n".join(lines) + "\n"


def _m3u8_headers():
    headers = _cdn_headers()
    headers["accept-encoding"] = "identity"
    return headers


def _stream_playlist(upstream_url, share_id):
    response = None
    try:
        session, _ = _get_cdn_session()
        response = (session or req).get(upstream_url, headers=_m3u8_headers(), timeout=20)
        if response.status_code != 200:
            return f"Media provider returned HTTP {response.status_code}", response.status_code
        if re.search(r'KEYFORMAT="(?!identity")[^\"]+"', response.text):
            return "This stream uses DRM. Open it in the official JioHotstar player.", 422
        body = rewrite_playlist(response.text, response.url or upstream_url, share_id)
        return Response(body, content_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-cache", "Access-Control-Allow-Origin": "*"})
    except Exception:
        return "Could not load the media playlist. Refresh the stream and try again.", 502
    finally:
        if response is not None:
            response.close()


@app.route("/proxy/<share_id>/master.m3u8")
def proxy_master(share_id):
    with _sess_lock:
        sess = _sessions.get(share_id)
    if not sess:
        return "session expired", 404
    return _stream_playlist(sess["m3u8_url"], share_id)


@app.route("/proxy/<share_id>/resource/<resource_id>")
def proxy_resource(share_id, resource_id):
    with _sess_lock:
        sess = _sessions.get(share_id)
        resource = sess.get("resources", {}).get(resource_id) if sess else None
    if not resource:
        return "media resource not found", 404
    upstream_url, playlist = resource
    return _stream_playlist(upstream_url, share_id) if playlist else _stream_from(upstream_url)


def _seg_headers():
    headers = _m3u8_headers()
    for name in ("Range", "If-Range"):
        if request.headers.get(name):
            headers[name] = request.headers[name]
    return headers


def _stream_from(upstream_url):
    """Relay media including fMP4 init fragments and byte-range responses."""
    response = None
    try:
        session, _ = _get_cdn_session()
        response = (session or req).get(upstream_url, headers=_seg_headers(), timeout=20, stream=True)
        if response.status_code not in (200, 206):
            status = response.status_code
            response.close()
            return f"Media provider returned HTTP {status}", status
        headers = {"Cache-Control": "no-cache", "Access-Control-Allow-Origin": "*"}
        for name in ("Content-Length", "Content-Range", "Accept-Ranges"):
            value = response.headers.get(name)
            if value:
                headers[name] = value
        def generate():
            try:
                for chunk in response.iter_content(65536):
                    if chunk:
                        yield chunk
            finally:
                response.close()
        result = Response(generate(), status=response.status_code,
                          content_type=response.headers.get("Content-Type", "application/octet-stream"), headers=headers)
        result.call_on_close(response.close)
        return result
    except Exception:
        if response is not None:
            response.close()
        return "Could not load media data. Refresh the stream and try again.", 502

@app.route("/api/ngrok/status")
def api_ngrok_status():
    with _ngrok_lock:
        url = _ngrok_url
    token_saved = bool(_load_ngrok_token())
    return jsonify({
        "active":      url is not None,
        "public_url":  url,
        "token_saved": token_saved,
    })

@app.route("/api/ngrok/set_token", methods=["POST"])
def api_ngrok_set_token():
    data  = request.json or {}
    token = data.get("token", "").strip()
    if not token:
        return jsonify({"error": "token is empty"}), 400
    _save_ngrok_token(token)
    url = start_ngrok(PORT)
    if url:
        return jsonify({"status": "tunnel started", "public_url": url})
    else:
        return jsonify({"status": "token saved, tunnel failed — check terminal"}), 207

@app.route("/api/ngrok/restart", methods=["POST"])
def api_ngrok_restart():
    stop_ngrok()
    time.sleep(1)
    url = start_ngrok(PORT)
    if url:
        return jsonify({"status": "restarted", "public_url": url})
    return jsonify({"error": "restart failed — check terminal"}), 502

@app.route("/api/token/status")
def api_token_status():
    _current_token()
    return jsonify(auth.token_status())


@app.route("/api/hotstar/live")
def api_hotstar_live():
    from hotstar_catalogue import discover_live_events, CatalogueError
    try:
        return jsonify(discover_live_events(_current_token(), refresh=request.args.get("refresh") == "1"))
    except CatalogueError as exc:
        return jsonify({"error": str(exc), "code": "catalogue_unavailable"}), exc.status


@app.route("/api/login/send_otp", methods=["POST"])
def api_login_send_otp():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "Expected a JSON object."}), 400
    try:
        auth.begin_login(data.get("phone", ""))
        return jsonify({"status": "otp_sent"})
    except auth.AuthError as exc:
        return jsonify({"error": str(exc), "code": exc.code}), exc.status


@app.route("/api/login/pending")
def api_login_pending():
    return jsonify(auth.pending_login())


@app.route("/api/login/verify_otp", methods=["POST"])
def api_login_verify_otp():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "Expected a JSON object."}), 400
    try:
        token = auth.finish_login(data.get("phone", ""), data.get("otp", ""))
        _current_token()
        return jsonify({"status": "logged_in", "remaining": tok_remaining(token)})
    except auth.AuthError as exc:
        return jsonify({"error": str(exc), "code": exc.code}), exc.status


@app.route("/api/login/use_existing", methods=["POST"])
def api_login_use_existing():
    token = _current_token()
    if not auth.authenticated_token(token):
        return jsonify({"error": "No valid saved token. Log in again.", "code": "login_required"}), 401
    return jsonify({"status": "loaded", "remaining": tok_remaining(token)})

@app.route("/api/sessions")
def api_sessions():
    now = time.time()
    with _sess_lock:
        out = []
        for sid, s in _sessions.items():
            out.append({
                "id":         sid,
                "title":      s.get("title",""),
                "lang":       s.get("lang",""),
                "age_min":    round((now - s["created_at"]) / 60, 1),
                "watch_link": f"{request.host_url.rstrip('/')}/watch/{sid}",
            })
    return jsonify(out)

# ---------------------------------------------------------------------------
# HTML — HOST PAGE (you use this to create links)
# ---------------------------------------------------------------------------

HOST_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Debloated Streaming</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#e6edf3;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;min-height:100vh;display:flex;flex-direction:column;align-items:center;padding:28px 16px;gap:20px}
h1{font-size:1.35rem;font-weight:700}
h1 span{background:linear-gradient(135deg,#1f6feb,#a371f7);-webkit-background-clip:text;-webkit-text-fill-color:transparent}

/* TAB BAR */
.tabs{display:flex;gap:2px;background:#161b22;border:1px solid #30363d;border-radius:10px;padding:4px;width:100%;max-width:700px}
.tab{flex:1;padding:9px 0;text-align:center;font-size:.85rem;font-weight:600;border-radius:7px;cursor:pointer;color:#8b949e;border:none;background:none;transition:all .15s}
.tab.active{background:#1f6feb;color:#fff}
.tab:hover:not(.active){color:#e6edf3;background:#21262d}

/* PANELS */
.panel{display:none;flex-direction:column;gap:16px;width:100%;max-width:700px}
.panel.active{display:flex}

.card{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:22px}
h2{font-size:.75rem;text-transform:uppercase;letter-spacing:.07em;color:#8b949e;margin-bottom:14px}

/* TOKEN STATUS BANNER */
.tok-banner{border-radius:10px;padding:14px 18px;display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.tok-banner.valid{background:rgba(63,185,80,.08);border:1px solid rgba(63,185,80,.25)}
.tok-banner.expired{background:rgba(248,81,73,.08);border:1px solid rgba(248,81,73,.25)}
.tok-banner.none{background:rgba(139,148,158,.08);border:1px solid #30363d}
.tok-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.tok-dot.valid{background:#3fb950}
.tok-dot.expired{background:#f85149}
.tok-dot.none{background:#8b949e}
.tok-info{display:flex;align-items:center;gap:10px;flex:1}
.tok-label{font-size:.88rem;font-weight:600}
.tok-sub{font-size:.75rem;color:#8b949e;margin-top:2px}
.tok-action{font-size:.78rem;background:#21262d;color:#e6edf3;border:none;border-radius:6px;padding:6px 12px;cursor:pointer;white-space:nowrap}
.tok-action:hover{background:#30363d}

input{width:100%;background:#0d1117;border:1px solid #30363d;border-radius:8px;color:#e6edf3;padding:10px 14px;font-size:.92rem;outline:none;margin-bottom:10px}
input:focus{border-color:#1f6feb}
select{width:100%;background:#0d1117;border:1px solid #30363d;border-radius:8px;color:#e6edf3;padding:10px 14px;font-size:.92rem;outline:none;margin-bottom:10px}

button.btn-primary{background:#1f6feb;color:#fff;border:none;border-radius:8px;padding:11px 22px;font-size:.9rem;font-weight:600;cursor:pointer;transition:opacity .15s;width:100%}
button.btn-primary:hover{opacity:.85}
button.btn-primary:disabled{opacity:.4;cursor:not-allowed}

.otp-row{display:flex;gap:10px}
.otp-row input{flex:1;margin-bottom:0}
.otp-row button{flex-shrink:0;background:#21262d;color:#e6edf3;border:none;border-radius:8px;padding:10px 16px;font-size:.85rem;font-weight:600;cursor:pointer;white-space:nowrap}
.otp-row button:hover{background:#30363d}

.divider{display:flex;align-items:center;gap:10px;color:#8b949e;font-size:.75rem;margin:4px 0}
.divider::before,.divider::after{content:'';flex:1;height:1px;background:#30363d}

.link-box{background:#010409;border:1px solid #21262d;border-radius:8px;padding:14px;font-family:monospace;font-size:.82rem;word-break:break-all;color:#79c0ff;position:relative}
.copy-btn{position:absolute;top:10px;right:10px;background:#21262d;color:#e6edf3;border:none;border-radius:6px;padding:5px 10px;font-size:.72rem;cursor:pointer}
.copy-btn:hover{background:#30363d}
.badge{font-size:.7rem;background:#21262d;border-radius:4px;padding:2px 6px;color:#8b949e}
.sessions-list{display:flex;flex-direction:column;gap:8px}
.sess-item{background:#010409;border:1px solid #21262d;border-radius:8px;padding:12px 14px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}
.sess-title{font-size:.88rem;color:#e6edf3}
.sess-meta{font-size:.73rem;color:#8b949e}
.sess-link{font-size:.73rem;color:#79c0ff;font-family:monospace}
.log{background:#010409;border:1px solid #21262d;border-radius:8px;padding:12px;font-size:.76rem;font-family:monospace;color:#8b949e;max-height:120px;overflow-y:auto;line-height:1.6}
.ok{color:#3fb950}.err{color:#f85149}.inf{color:#79c0ff}
.row{display:flex;gap:10px}
.row input{flex:1;margin-bottom:0}
.row select{width:auto;flex:1;margin-bottom:0}
</style>
</head>
<body>

<h1>Debloated <span>Streaming</span></h1>

<!-- TAB BAR -->
<div class="tabs">
  <button class="tab active" id="tab-login" onclick="switchTab('login')">Login</button>
  <button class="tab" id="tab-stream" onclick="switchTab('stream')">Stream</button>
</div>

<!-- LOGIN PANEL -->
<div class="panel active" id="panel-login">

  <!-- token status -->
  <div class="card">
    <h2>Account Status</h2>
    <div class="tok-banner none" id="tokBanner">
      <div class="tok-info">
        <div class="tok-dot none" id="tokDot"></div>
        <div>
          <div class="tok-label" id="tokLabel">Checking...</div>
          <div class="tok-sub" id="tokSub"></div>
        </div>
      </div>
      <button class="tok-action" id="tokUseBtn" onclick="useExistingToken()" style="display:none">Use This Token</button>
    </div>
  </div>

  <!-- OTP login -->
  <div class="card">
    <h2>Login with Phone OTP</h2>
    <input id="phoneInput" placeholder="10-digit mobile number" maxlength="10" inputmode="numeric" />
    <button class="btn-primary" id="btnSendOtp" onclick="sendOtp()">Send OTP</button>

    <div id="otpSection" style="display:none;margin-top:12px">
      <div class="otp-row" style="margin-bottom:10px">
        <input id="otpInput" placeholder="Enter OTP" maxlength="6" inputmode="numeric" />
        <button onclick="sendOtp()">Resend</button>
      </div>
      <button class="btn-primary" id="btnVerify" onclick="verifyOtp()">Verify OTP</button>
    </div>
  </div>

  <div class="card">
    <div class="log" id="loginLog"></div>
  </div>

</div>

<!-- STREAM PANEL -->
<div class="panel" id="panel-stream">

  <div class="card">
    <h2>Create Share Link</h2>
    <input id="urlInput" placeholder="paste hotstar live URL..." />
    <div class="row" style="margin-bottom:10px">
      <select id="langSel">
        <option value="eng">English</option>
        <option value="hin">Hindi</option>
        <option value="tam">Tamil</option>
        <option value="tel">Telugu</option>
        <option value="kan">Kannada</option>
        <option value="mal">Malayalam</option>
      </select>
      <button class="btn-primary" id="btnCreate" onclick="createLink()" style="width:auto;padding:10px 20px">Generate</button>
    </div>
    <div id="linkOut" style="display:none">
      <p style="font-size:.73rem;color:#8b949e;margin-bottom:6px">Local (you)</p>
      <div class="link-box" id="linkBox" style="margin-bottom:10px">
        <button class="copy-btn" onclick="copyLink('linkText')">copy</button>
        <div id="linkText"></div>
      </div>
      <div id="publicLinkWrap" style="display:none">
        <p style="font-size:.73rem;color:#8b949e;margin-bottom:6px">Share with friends (ngrok)</p>
        <div class="link-box">
          <button class="copy-btn" onclick="copyLink('publicLinkText')">copy</button>
          <div id="publicLinkText"></div>
        </div>
      </div>
      <div style="display:flex;gap:8px;margin-top:10px">
        <button onclick="refreshStream()" style="background:#21262d;color:#e6edf3;border:none;border-radius:8px;padding:8px 14px;font-size:.78rem;cursor:pointer">Refresh Token</button>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>ngrok — Share Outside LAN</h2>
    <div id="ngrokStatus" style="font-size:.82rem;color:#8b949e;margin-bottom:12px">checking...</div>
    <input id="ngrokToken" placeholder="paste ngrok authtoken" type="password" />
    <button class="btn-primary" onclick="saveNgrokToken()">Save and Start Tunnel</button>
    <div id="ngrokActiveWrap" style="display:none;margin-top:12px">
      <div class="link-box">
        <button class="copy-btn" onclick="copyNgrokUrl()">copy</button>
        <div id="ngrokUrlText"></div>
      </div>
      <button onclick="restartNgrok()" style="margin-top:8px;background:#21262d;color:#e6edf3;border:none;border-radius:8px;padding:7px 14px;font-size:.78rem;cursor:pointer">Restart Tunnel</button>
    </div>
    <p style="font-size:.71rem;color:#8b949e;margin-top:10px">Free account at <a href="https://ngrok.com" target="_blank" style="color:#79c0ff">ngrok.com</a>. Token saved locally.</p>
  </div>

  <div class="card">
    <h2>Active Links</h2>
    <div class="sessions-list" id="sessionsDiv"><div style="color:#8b949e;font-size:.85rem">none yet</div></div>
  </div>

  <div class="card">
    <div class="log" id="log"></div>
  </div>

</div>

<script>
let lastShareId = null;

// --- tab switching ---
function switchTab(t) {
  ['login','stream'].forEach(n => {
    document.getElementById('tab-'+n).classList.toggle('active', n===t);
    document.getElementById('panel-'+n).classList.toggle('active', n===t);
  });
  if (t==='stream') loadSessions();
}

function log(msg, cls='', el='log') {
  const e = document.getElementById(el);
  const d = document.createElement('div');
  d.className = cls;
  d.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
  e.appendChild(d);
  e.scrollTop = e.scrollHeight;
}

async function loadTokenStatus() {
  try {
    const r = await fetch('/api/token/status');
    const d = await r.json();
    const banner = document.getElementById('tokBanner');
    const dot    = document.getElementById('tokDot');
    const label  = document.getElementById('tokLabel');
    const sub    = document.getElementById('tokSub');
    const btn    = document.getElementById('tokUseBtn');

    banner.className = 'tok-banner ' + d.status;
    dot.className    = 'tok-dot ' + d.status;

    if (d.status === 'valid') {
      label.textContent = 'Logged in';
      sub.textContent   = d.remaining;
      btn.style.display = 'none';
    } else if (d.status === 'expired') {
      label.textContent = 'Token expired';
      sub.textContent   = 'Login again to continue';
      btn.style.display = 'none';
    } else {
      label.textContent = 'Not logged in';
      sub.textContent   = 'Login below or load an existing token file';
      btn.style.display = 'block';
    }
  } catch(e) {}
}

async function useExistingToken() {
  try {
    const r = await fetch('/api/login/use_existing', {method:'POST'});
    const d = await r.json();
    if (d.error) { log(d.error, 'err', 'loginLog'); return; }
    log('Token loaded. ' + d.remaining, 'ok', 'loginLog');
    loadTokenStatus();
    setTimeout(()=> switchTab('stream'), 800);
  } catch(e) { log(e.message, 'err', 'loginLog'); }
}

async function sendOtp() {
  const phone = document.getElementById('phoneInput').value.trim();
  if (!phone) { log('enter phone number', 'err', 'loginLog'); return; }
  document.getElementById('btnSendOtp').disabled = true;
  log('sending OTP to ' + phone + '...', 'inf', 'loginLog');
  try {
    const r = await fetch('/api/login/send_otp', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({phone})
    });
    const d = await r.json();
    if (d.error) { log(d.error, 'err', 'loginLog'); return; }
    log('Hotstar accepted the OTP request. Check your SMS.', 'ok', 'loginLog');
    document.getElementById('otpSection').style.display = 'block';
    document.getElementById('otpInput').focus();
  } catch(e) { log(e.message, 'err', 'loginLog'); }
  finally { document.getElementById('btnSendOtp').disabled = false; }
}

async function verifyOtp() {
  const phone = document.getElementById('phoneInput').value.trim();
  const otp   = document.getElementById('otpInput').value.trim();
  if (!otp) { log('enter the OTP', 'err', 'loginLog'); return; }
  document.getElementById('btnVerify').disabled = true;
  log('verifying OTP...', 'inf', 'loginLog');
  try {
    const r = await fetch('/api/login/verify_otp', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({phone, otp})
    });
    const d = await r.json();
    if (d.error) { log(d.error, 'err', 'loginLog'); return; }
    log('Logged in! ' + d.remaining, 'ok', 'loginLog');
    loadTokenStatus();
    document.getElementById('otpSection').style.display = 'none';
    document.getElementById('otpInput').value = '';
    setTimeout(()=> switchTab('stream'), 900);
  } catch(e) { log(e.message, 'err', 'loginLog'); }
  finally { document.getElementById('btnVerify').disabled = false; }
}

async function createLink() {
  const url  = document.getElementById('urlInput').value.trim();
  const lang = document.getElementById('langSel').value;
  if (!url) { log('paste a URL first', 'err'); return; }
  document.getElementById('btnCreate').disabled = true;
  log('fetching stream...', 'inf');
  try {
    const r = await fetch('/api/create', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({url, lang})
    });
    const d = await r.json();
    if (d.error) { log(d.error, 'err'); return; }
    lastShareId = d.share_id;
    document.getElementById('linkText').textContent = d.watch_link;
    document.getElementById('linkOut').style.display = 'block';
    if (d.public_link) {
      document.getElementById('publicLinkText').textContent = d.public_link;
      document.getElementById('publicLinkWrap').style.display = 'block';
      log('public link: ' + d.public_link, 'ok');
    } else {
      document.getElementById('publicLinkWrap').style.display = 'none';
      log('link created: ' + d.watch_link, 'ok');
    }
    loadSessions();
  } catch(e) { log(e.message, 'err'); }
  finally { document.getElementById('btnCreate').disabled = false; }
}

async function refreshStream() {
  if (!lastShareId) return;
  log('refreshing...', 'inf');
  const r = await fetch(`/api/refresh/${lastShareId}`, {method:'POST'});
  const d = await r.json();
  log(d.error || 'refreshed', d.error ? 'err' : 'ok');
}

function copyLink(elId) {
  navigator.clipboard.writeText(document.getElementById(elId).textContent)
    .then(()=> log('copied', 'ok'));
}

async function loadNgrokStatus() {
  try {
    const r = await fetch('/api/ngrok/status');
    const d = await r.json();
    const statusEl   = document.getElementById('ngrokStatus');
    const activeWrap = document.getElementById('ngrokActiveWrap');
    if (d.active) {
      statusEl.innerHTML = '<span style="color:#3fb950">Tunnel active</span>';
      document.getElementById('ngrokUrlText').textContent = d.public_url;
      activeWrap.style.display = 'block';
    } else {
      statusEl.textContent = d.token_saved ? 'Token saved but tunnel is down. Click Restart.' : 'No tunnel. Paste authtoken below.';
      activeWrap.style.display = d.token_saved ? 'block' : 'none';
    }
  } catch(e) {}
}

async function saveNgrokToken() {
  const token = document.getElementById('ngrokToken').value.trim();
  if (!token) { log('paste your ngrok token first', 'err'); return; }
  const r = await fetch('/api/ngrok/set_token', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({token})});
  const d = await r.json();
  if (d.public_url) { log('ngrok up: ' + d.public_url, 'ok'); document.getElementById('ngrokToken').value=''; }
  else log(d.status || d.error || 'check terminal', d.error?'err':'');
  loadNgrokStatus();
}

async function restartNgrok() {
  const r = await fetch('/api/ngrok/restart', {method:'POST'});
  const d = await r.json();
  log(d.public_url ? 'restarted: '+d.public_url : (d.error||'failed'), d.error?'err':'ok');
  loadNgrokStatus();
}

function copyNgrokUrl() {
  navigator.clipboard.writeText(document.getElementById('ngrokUrlText').textContent)
    .then(()=> log('ngrok URL copied', 'ok'));
}

async function loadSessions() {
  const r = await fetch('/api/sessions');
  const sessions = await r.json();
  const div = document.getElementById('sessionsDiv');
  if (!sessions.length) { div.innerHTML = '<div style="color:#8b949e;font-size:.85rem">none yet</div>'; return; }
  div.innerHTML = sessions.map(s => `
    <div class="sess-item">
      <div>
        <div class="sess-title">${s.title} <span class="badge">${s.lang}</span></div>
        <div class="sess-link">${s.watch_link}</div>
        <div class="sess-meta">${s.age_min} min old</div>
      </div>
      <button onclick="navigator.clipboard.writeText('${s.watch_link}')" style="background:#21262d;color:#e6edf3;border:none;border-radius:6px;padding:5px 10px;font-size:.75rem;cursor:pointer">copy</button>
    </div>`).join('');
}

setInterval(loadSessions, 10000);
setInterval(loadNgrokStatus, 15000);
setInterval(loadTokenStatus, 30000);
loadTokenStatus();
loadNgrokStatus();

// auto-switch to stream tab if token already valid
fetch('/api/token/status').then(r=>r.json()).then(d=>{
  if (d.status==='valid') switchTab('stream');
});
</script>
</body>
</html>"""

# ---------------------------------------------------------------------------
# HTML — WATCH PAGE (family opens this)
# ---------------------------------------------------------------------------

WATCH_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d0d0d;display:flex;flex-direction:column;align-items:center;min-height:100vh;font-family:system-ui,sans-serif}

/* top bar */
#topbar{width:100%;max-width:1280px;display:flex;align-items:center;gap:10px;padding:10px 14px}
#title-text{color:#e6edf3;font-size:.95rem;font-weight:600;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#live-badge{background:#e53935;color:#fff;font-size:.65rem;font-weight:700;padding:2px 7px;border-radius:3px;letter-spacing:.05em;display:none}
#kbps-info{color:#8b949e;font-size:.72rem;font-family:monospace}

/* video wrap */
#wrap{position:relative;width:100%;max-width:1280px;background:#000;border-radius:6px;overflow:hidden;cursor:pointer}
video{width:100%;display:block;background:#000}

/* custom controls bar */
#ctrl{position:absolute;bottom:0;left:0;right:0;background:linear-gradient(transparent,rgba(0,0,0,.82));padding:6px 12px 10px;display:flex;flex-direction:column;gap:6px;opacity:0;transition:opacity .25s;user-select:none}
#wrap:hover #ctrl,#wrap.show-ctrl #ctrl{opacity:1}

/* progress bar (live = decorative scrubber over DVR window) */
#prog-row{display:flex;align-items:center;gap:8px}
#prog{flex:1;-webkit-appearance:none;appearance:none;height:4px;border-radius:2px;background:rgba(255,255,255,.25);outline:none;cursor:pointer}
#prog::-webkit-slider-thumb{-webkit-appearance:none;width:12px;height:12px;border-radius:50%;background:#e53935;cursor:pointer}
#time-txt{color:#ccc;font-size:.7rem;font-family:monospace;white-space:nowrap;min-width:80px;text-align:right}

/* bottom row */
#bot-row{display:flex;align-items:center;gap:10px}
.ctl-btn{background:none;border:none;color:#fff;cursor:pointer;padding:4px;display:flex;align-items:center;justify-content:center;border-radius:4px;transition:background .15s;flex-shrink:0}
.ctl-btn:hover{background:rgba(255,255,255,.12)}
.ctl-btn svg{width:20px;height:20px;fill:currentColor}

/* volume */
#vol-wrap{display:flex;align-items:center;gap:6px}
#vol{-webkit-appearance:none;appearance:none;width:80px;height:4px;border-radius:2px;background:rgba(255,255,255,.3);outline:none;cursor:pointer}
#vol::-webkit-slider-thumb{-webkit-appearance:none;width:12px;height:12px;border-radius:50%;background:#fff;cursor:pointer}

/* quality picker */
#qual-wrap{position:relative;margin-left:auto}
#qual-btn{background:rgba(255,255,255,.1);border:none;color:#fff;font-size:.72rem;padding:4px 8px;border-radius:4px;cursor:pointer;white-space:nowrap}
#qual-btn:hover{background:rgba(255,255,255,.2)}
#qual-menu{display:none;position:absolute;bottom:calc(100% + 6px);right:0;background:#1e1e1e;border:1px solid #333;border-radius:6px;min-width:120px;overflow:hidden;z-index:10;box-shadow:0 4px 16px rgba(0,0,0,.6)}
#qual-menu.open{display:block}
.q-item{padding:8px 14px;color:#ddd;font-size:.8rem;cursor:pointer;white-space:nowrap}
.q-item:hover{background:#333}
.q-item.active{color:#e53935;font-weight:700}

/* status */
#status{color:#555;font-family:monospace;font-size:.72rem;padding:6px 0 14px;text-align:center}
</style>
</head>
<body>

<div id="topbar">
  <span id="title-text">__TITLE__</span>
  <span id="live-badge">&#9679; LIVE</span>
  <span id="kbps-info"></span>
</div>

<div id="wrap">
  <video id="v" autoplay playsinline></video>

  <div id="ctrl">
    <!-- progress -->
    <div id="prog-row">
      <input type="range" id="prog" min="0" max="1000" value="0" step="1">
      <span id="time-txt">LIVE</span>
    </div>

    <!-- bottom row -->
    <div id="bot-row">
      <!-- play/pause -->
      <button class="ctl-btn" id="pp-btn" title="Play/Pause">
        <svg id="pp-icon" viewBox="0 0 24 24"><path d="M6 19h4V5H6zm8-14v14h4V5z"/></svg>
      </button>

      <!-- volume -->
      <div id="vol-wrap">
        <button class="ctl-btn" id="mute-btn" title="Mute">
          <svg id="mute-icon" viewBox="0 0 24 24"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/></svg>
        </button>
        <input type="range" id="vol" min="0" max="100" value="100" step="1">
      </div>

      <!-- quality picker -->
      <div id="qual-wrap">
        <button id="qual-btn">Auto &#9660;</button>
        <div id="qual-menu"></div>
      </div>

      <!-- fullscreen -->
      <button class="ctl-btn" id="fs-btn" title="Fullscreen" style="margin-left:4px">
        <svg id="fs-icon" viewBox="0 0 24 24"><path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg>
      </button>
    </div>
  </div>
</div>

<div id="status">connecting&#8230;</div>

<script>
const video   = document.getElementById('v');
const wrap    = document.getElementById('wrap');
const status  = document.getElementById('status');
const kbpsEl  = document.getElementById('kbps-info');
const liveBadge = document.getElementById('live-badge');
const ppBtn   = document.getElementById('pp-btn');
const ppIcon  = document.getElementById('pp-icon');
const muteBtn = document.getElementById('mute-btn');
const muteIcon= document.getElementById('mute-icon');
const volSlider = document.getElementById('vol');
const prog    = document.getElementById('prog');
const timeTxt = document.getElementById('time-txt');
const qualBtn = document.getElementById('qual-btn');
const qualMenu= document.getElementById('qual-menu');
const fsBtn   = document.getElementById('fs-btn');
const fsIcon  = document.getElementById('fs-icon');
const src     = '/proxy/__SHARE_ID__/master.m3u8';

let hls = null;
let isLive = true;

const ICONS = {
  play:'<path d="M8 5v14l11-7z"/>',
  pause:'<path d="M6 19h4V5H6zm8-14v14h4V5z"/>',
  volOn:'<path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/>',
  volOff:'<path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z"/>',
  fsIn:'<path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/>',
  fsOut:'<path d="M5 16h3v3h2v-5H5v2zm3-8H5v2h5V5H8v3zm6 11h2v-3h3v-2h-5v5zm2-11V5h-2v5h5V8h-3z"/>'
};
function setIcon(el, key){ el.innerHTML = ICONS[key]; }

ppBtn.addEventListener('click', ()=>{ video.paused ? video.play() : video.pause(); });
video.addEventListener('play',  ()=>{ setIcon(ppIcon,'pause'); });
video.addEventListener('pause', ()=>{ setIcon(ppIcon,'play'); });

volSlider.addEventListener('input', ()=>{
  video.volume = volSlider.value / 100;
  video.muted = (volSlider.value == 0);
  syncMuteIcon();
});
muteBtn.addEventListener('click', ()=>{
  video.muted = !video.muted;
  if (!video.muted && video.volume === 0) { video.volume = 0.5; volSlider.value = 50; }
  syncMuteIcon();
});
function syncMuteIcon(){
  setIcon(muteIcon, (video.muted || video.volume === 0) ? 'volOff' : 'volOn');
  if (!video.muted) volSlider.value = Math.round(video.volume * 100);
}

/* progress bar seeks within DVR window */
video.addEventListener('timeupdate', ()=>{
  if (!isLive || video.duration === Infinity) {
    timeTxt.textContent = 'LIVE';
    prog.value = 1000;
    return;
  }
  const pct = video.duration > 0 ? video.currentTime / video.duration : 1;
  prog.value = Math.round(pct * 1000);
  const s = Math.floor(video.currentTime);
  timeTxt.textContent = `${String(Math.floor(s/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;
});
prog.addEventListener('input', ()=>{
  if (video.duration && video.duration !== Infinity) {
    video.currentTime = (prog.value / 1000) * video.duration;
  }
});

fsBtn.addEventListener('click', ()=>{
  if (!document.fullscreenElement) {
    wrap.requestFullscreen().catch(()=>{});
    setIcon(fsIcon,'fsOut');
  } else {
    document.exitFullscreen();
    setIcon(fsIcon,'fsIn');
  }
});
document.addEventListener('fullscreenchange', ()=>{
  setIcon(fsIcon, document.fullscreenElement ? 'fsOut' : 'fsIn');
});
wrap.addEventListener('dblclick', ()=>{ fsBtn.click(); });
wrap.addEventListener('click', e=>{
  if (e.target.closest('#ctrl')) return;
  ppBtn.click();
});

function buildQualMenu(levels, currentLevel){
  qualMenu.innerHTML = '';
  const auto = document.createElement('div');
  auto.className = 'q-item' + (currentLevel === -1 ? ' active' : '');
  auto.textContent = 'Auto';
  auto.dataset.lvl = '-1';
  qualMenu.appendChild(auto);
  levels.forEach((lvl, i)=>{
    const el = document.createElement('div');
    el.className = 'q-item' + (currentLevel === i ? ' active' : '');
    const label = lvl.height ? `${lvl.height}p` : `${Math.round(lvl.bitrate/1000)}k`;
    el.textContent = label;
    el.dataset.lvl = i;
    qualMenu.appendChild(el);
  });
  qualMenu.querySelectorAll('.q-item').forEach(item=>{
    item.addEventListener('click', ()=>{
      const lvl = parseInt(item.dataset.lvl);
      hls.currentLevel = lvl;
      qualBtn.textContent = (lvl === -1 ? 'Auto' : item.textContent) + ' \u25be';
      qualMenu.querySelectorAll('.q-item').forEach(x=>x.classList.remove('active'));
      item.classList.add('active');
      qualMenu.classList.remove('open');
    });
  });
}
qualBtn.addEventListener('click', e=>{ e.stopPropagation(); qualMenu.classList.toggle('open'); });
document.addEventListener('click', ()=> qualMenu.classList.remove('open'));

function setStatus(msg){ status.textContent = msg; }

if (Hls.isSupported()) {
  hls = new Hls({
    lowLatencyMode: true,
    liveSyncDurationCount: 3,
    liveMaxLatencyDurationCount: 6,
    maxBufferLength: 30,
  });
  hls.loadSource(src);
  hls.attachMedia(video);

  hls.on(Hls.Events.MANIFEST_PARSED, (e, data)=>{
    isLive = hls.levels.some(l=>l.details && l.details.live) || true;
    liveBadge.style.display = 'inline-block';
    setStatus('');
    buildQualMenu(hls.levels, hls.currentLevel);
    video.play().catch(()=>{});
  });

  hls.on(Hls.Events.LEVEL_SWITCHED, (e, data)=>{
    const lvl = hls.levels[data.level];
    if (lvl) {
      const label = `${lvl.height}p @ ${Math.round(lvl.bitrate/1000)} kbps`;
      kbpsEl.textContent = label;
      setStatus('');
    }
    buildQualMenu(hls.levels, data.level);
    if (hls.autoLevelEnabled) {
      const lvl = hls.levels[data.level];
      qualBtn.textContent = 'Auto (' + (lvl ? lvl.height+'p' : data.level) + ') \u25be';
    }
  });

  hls.on(Hls.Events.ERROR, (e, data)=>{
    if (data.fatal) {
      setStatus('\u26a0 ' + data.type + ' \u2014 ' + data.details);
      if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
        setTimeout(()=> hls.startLoad(), 2000);
      } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
        hls.recoverMediaError();
      }
    }
  });

} else if (video.canPlayType('application/vnd.apple.mpegurl')) {
  video.src = src;
  video.play().catch(()=>{});
  liveBadge.style.display='inline-block';
  setStatus('playing (native HLS)');
} else {
  setStatus('browser does not support HLS');
}
</script>
</body>
</html>"""

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import socket
    import webbrowser
    import atexit

    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        local_ip = "localhost"

    def _ngrok_startup():
        time.sleep(0.8)  # let Flask bind before attempting tunnel
        url = start_ngrok(PORT)
        if url:
            print(f"\n  [ngrok] friends link: {url}/watch/<id>  (after you create a link)\n")
        else:
            print(f"\n  [ngrok] no tunnel — paste authtoken in the web UI to enable\n")

    if os.environ.get("HOTSTAR_ENABLE_TUNNEL") == "1":
        threading.Thread(target=_ngrok_startup, daemon=True).start()
    atexit.register(stop_ngrok)

    print(f"""
  Debloated Streaming — Hotstar Relay
  ----------------------
  you:     http://localhost:{PORT}
  playback: local browser, MPV or VLC through this service
  Ctrl+C to stop
""")
    threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(f"http://localhost:{PORT}")), daemon=True).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
