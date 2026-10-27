"""Shared, persistent OTP login for the terminal and local relay."""
import base64
from importlib import import_module
import json
import os
from pathlib import Path
import re
import tempfile
import time
import uuid

import requests
from urllib.parse import urlparse
from hotstar_login_schema import provider_error, binary_error, otp_confirmed, find_widget_action

APP_DIR = Path(__file__).resolve().parent
TOKEN_PATH = APP_DIR / "hotstar_token.json"
SESSION_PATH = APP_DIR / "hotstar_guest.json"
SESSION_TTL = 600
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


class AuthError(Exception):
    def __init__(self, message, code="auth_failed", status=400):
        super().__init__(message)
        self.code, self.status = code, status


def jwt_payload(token):
    try:
        part = token.split(".")[1]
        value = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError, IndexError, AttributeError):
        return {}


def jwt_exp(token):
    try:
        return float(jwt_payload(token).get("exp", 0))
    except (ValueError, TypeError):
        return 0


def tok_valid(token):
    return bool(token) and jwt_exp(token) > time.time() + 60


def tok_remaining(token):
    hours = (jwt_exp(token) - time.time()) / 3600
    return f"{hours:.1f}h remaining" if hours > 0 else "expired"


def _subject(token):
    payload = jwt_payload(token)
    subject = payload.get("sub", {})
    if isinstance(subject, str):
        try:
            subject = json.loads(subject)
        except ValueError:
            subject = {}
    return {**payload, **(subject if isinstance(subject, dict) else {})}


def get_device_id_from_token(token):
    claims = _subject(token)
    return str(claims.get("deviceId") or claims.get("device_id") or uuid.uuid4())


def authenticated_token(token, guest=None):
    if not tok_valid(token) or token == guest:
        return False
    claims = {str(k).lower().replace("_", ""): v for k, v in _subject(token).items()}
    if any(claims.get(k) is True for k in ("isguest", "guest", "isanonymous")):
        return False
    kinds = (str(claims.get(key) or "").lower()
             for key in ("usertype", "accounttype", "type", "customertype"))
    if any(kind in ("guest", "anonymous", "visitor") for kind in kinds):
        return False

    def has_identity(fields):
        values = (claims.get(key) for key in fields)
        return any(str(value).lower() not in ("", "none", "null", "0", "guest", "anonymous")
                   for value in values if value is not None)

    generic_identity = has_identity(("userid", "accountid", "profileid", "uid"))
    # Current Hotstar JWTs use hId/pId, including on some anonymous sessions.
    # Require a verified login identity to distinguish these from guest tokens.
    hotstar_identity = has_identity(("hid", "pid", "dwhid", "dwpid"))
    verified = claims.get("isphoneverified") is True or claims.get("isemailverified") is True
    return generic_identity or (hotstar_identity and verified)


def _read_json(path):
    try:
        with Path(path).open(encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _write_json(path, value):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_token(path=None):
    paths = [Path(path)] if path else [TOKEN_PATH, APP_DIR / "hs_token.json"]
    expired = None
    for candidate in paths:
        data = _read_json(candidate) or {}
        token = data.get("user_token") or data.get("token")
        if isinstance(token, str) and jwt_payload(token):
            if tok_valid(token):
                return token
            expired = expired or token
    return expired


def save_token_file(token, phone="", path=None):
    if not authenticated_token(token):
        raise AuthError("Hotstar did not return an authenticated account token. Please sign in again.", "guest_token", 401)
    destination = Path(path) if path else TOKEN_PATH
    _write_json(destination, {"user_token": token, "phone": phone,
                             "saved_at": int(time.time()), "expires_at": jwt_exp(token)})
    return str(destination)


def token_status(path=None):
    token = load_token(path)
    if not token:
        return {"status": "none", "remaining": "", "has_token": False}
    valid = tok_valid(token) and authenticated_token(token)
    return {"status": "valid" if valid else "expired",
            "remaining": tok_remaining(token) if valid else "expired", "has_token": True}


def normalize_phone(phone):
    phone = re.sub(r"[\s()-]", "", str(phone or ""))
    if phone.startswith("+91"):
        phone = phone[3:]
    if not re.fullmatch(r"[6-9][0-9]{9}", phone):
        raise AuthError("Enter a valid 10-digit Indian mobile number.", "invalid_phone")
    return phone


def normalize_otp(otp):
    otp = str(otp or "").strip()
    if not re.fullmatch(r"[0-9]{4,6}", otp):
        raise AuthError("Enter the 4- to 6-digit OTP sent by Hotstar.", "invalid_otp")
    return otp


def _safe_message(value):
    text = _JWT.sub("[redacted]", str(value))
    text = re.sub(r"\b[0-9]{10,}\b", "[redacted]", text)
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    return text[:240] if "<" not in text else ""


def _provider_error(data):
    problem = provider_error(data)
    return _safe_message(problem) if problem else None


def _find_token(response, data=None):
    token = response.headers.get("x-hs-updatedusertoken")
    if token:
        return token
    raw = json.dumps(data) if data is not None else response.content.decode("utf-8", "replace")
    candidates = _JWT.findall(raw)
    return max(candidates, key=len) if candidates else None


class LoginClient:
    def __init__(self, state=None, session=None):
        self.state = dict(state or {})
        self.state.setdefault("device_id", str(uuid.uuid4()))
        self.session = session or requests.Session()
        for cookie in self.state.get("cookies", []):
            self.session.cookies.set_cookie(requests.cookies.create_cookie(**cookie))

    def _headers(self, method):
        live = import_module("hotstar_live")
        guest = self.state.get("guest")
        if method == "web":
            headers = live._dl_web_hdrs(token=guest, ps=self.state.get("ps"))
            headers["x-hs-device-id"] = self.state["device_id"]
        else:
            headers = live._dl_android_hdrs(token=guest)
            headers["X-HS-Device-Id"] = self.state["device_id"]
        # The service supports its typed JSON messages on both platforms. The
        # previous hand-written protobuf did not serialize the request envelope.
        headers.update({"Content-Type": "application/json", "Accept": "application/json"})
        return headers

    def _response(self, response, method):
        try:
            data = response.json()
        except (ValueError, TypeError):
            data = None
        self._update(response)
        problem = _provider_error(data)
        if data is None:
            binary_problem = binary_error(response.content)
            if binary_problem:
                problem = _safe_message(binary_problem)
        if response.status_code == 429:
            raise AuthError("Hotstar is limiting login requests. Wait before requesting another OTP.", "rate_limited", 429)
        if response.status_code not in (200, 201, 202):
            detail = f"Hotstar rejected the request (HTTP {response.status_code})."
            raise AuthError(detail + (" " + problem if problem else ""), "provider_error", 502)
        if problem:
            raise AuthError("Hotstar: " + problem, "provider_error", 400)
        html_response = data is None and response.content.lstrip().startswith(b"<")
        if html_response or (method == "web" and not isinstance(data, dict)):
            raise AuthError("Hotstar returned a browser verification page. Sign in using the official Hotstar website and try again.", "browser_verification", 502)
        return response, data

    def _request(self, verb, url, method, **kwargs):
        try:
            response = getattr(self.session, verb)(url, headers=self._headers(method), timeout=20, **kwargs)
        except requests.RequestException as exc:
            detail = "Hotstar timed out." if isinstance(exc, requests.Timeout) else "Could not connect to Hotstar."
            raise AuthError(detail + " Check your connection and try again.", "network_error", 502) from None
        return self._response(response, method)

    def _post(self, url, method, **kwargs):
        return self._request("post", url, method, **kwargs)

    def _action_url(self, route, method):
        parsed = urlparse(str(route or ""))
        if parsed.scheme or parsed.netloc or not parsed.path.startswith("/v2/") or ".." in parsed.path.split("/"):
            raise AuthError("Hotstar returned an invalid login action. Please start login again.", "invalid_login_action", 502)
        base = "https://apix.hotstar.com" if method == "android" else "https://www.hotstar.com/api/internal/bff"
        return base + route

    def _login_action(self, method):
        _, data = self._request("get", self._action_url("/v2/pages/login", method), method)
        route = find_widget_action(data, "sendOtp")
        if not route:
            raise AuthError("Hotstar did not provide a phone login action. Use the official Hotstar login page.", "login_unavailable", 502)
        # Browser challenges are handled by Hotstar's own UI, never guessed.
        def needs_browser(value):
            if isinstance(value, dict):
                return "generate_recaptcha_token_action" in value or any(needs_browser(v) for v in value.values())
            if isinstance(value, list):
                return any(needs_browser(v) for v in value)
            return False
        if needs_browser(data):
            raise AuthError("Hotstar requires browser verification before sending an OTP. Use the official Hotstar login page.", "browser_verification", 502)
        self.state["send_url"] = route
        return route

    def guest(self):
        try:
            response, data = self._post("https://www.hotstar.com/api/internal/bff/v2/freshstart", "web",
                params={"journey": "login"}, json={"deeplink_url": "", "context": {
                    "url": "type.googleapis.com/context.StateContext", "value": "CgQaAggC"}, "app_launch_count": 1})
            if not tok_valid(_find_token(response, data)):
                raise AuthError("Hotstar did not create a valid web login session.", "no_guest", 502)
            method = "web"
        except AuthError as exc:
            if exc.code == "rate_limited":
                raise
            response, data = self._post("https://apix.hotstar.com/v2/freshstart", "android",
                params={"client_capabilities": json.dumps({"package": ["dash", "hls"], "container": ["fmp4", "ts"],
                    "encryption": ["plain", "widevine"], "video_codec": ["h264"], "ladder": ["phone"],
                    "resolution": ["sd", "hd", "fhd"], "dynamic_range": ["sdr"]}),
                    "drm_parameters": json.dumps({"widevine_security_level": ["HW_SECURE_ALL", "SW_SECURE_DECODE"],
                    "hdcp_version": ["HDCP_V2_2"]}), "subs": "null", "login": "UNKNOWN"}, json={})
            method = "android"
        token = _find_token(response, data)
        if not tok_valid(token):
            raise AuthError("Hotstar did not create a valid login session. Please try again.", "no_guest", 502)
        self.state.update(guest=token, method=method)
        self._update(response)
        return token, self.state.get("ps"), method

    def _update(self, response):
        token = response.headers.get("x-hs-updatedusertoken")
        if tok_valid(token):
            self.state["guest"] = token
        proxystate = response.headers.get("x-hs-setproxystate") or response.headers.get("x-hs-proxystate")
        if proxystate:
            self.state["ps"] = proxystate

    def send(self, phone):
        phone = normalize_phone(phone)
        method = self.state.get("method", "web")
        route = self._login_action(method)
        response, data = self._post(self._action_url(route, method), method,
            json={"body": {"@type": "type.googleapis.com/feature.login.InitiatePhoneLoginRequest",
                "initiate_by": 0, "recaptcha_token": "", "phone_number": phone}})
        if not otp_confirmed(data):
            raise AuthError("Hotstar did not confirm the OTP request. No SMS delivery was confirmed. Try the official Hotstar login page.", "otp_not_confirmed", 502)
        self._update(response)
        verify_route = find_widget_action(data, "verifyOtp")
        if verify_route:
            self._action_url(verify_route, method)
        self.state.update(phone=phone, created_at=time.time(), otp_confirmed=True,
                          verify_url=verify_route or "/v2/pages/1/spaces/1/widgets/9?action=verifyOtp&page_enum=login")
        return True, method

    def verify(self, phone, otp):
        phone, otp = normalize_phone(phone), normalize_otp(otp)
        if self.state.get("phone") and self.state["phone"] != phone:
            raise AuthError("The phone number changed. Request another OTP.", "phone_mismatch")
        method = self.state.get("method", "web")
        guest = self.state.get("guest")
        route = self.state.get("verify_url") or "/v2/pages/1/spaces/1/widgets/9?action=verifyOtp&page_enum=login"
        response, data = self._post(self._action_url(route, method), method,
            json={"body": {"@type": "type.googleapis.com/feature.login.VerifyPhoneLoginRequest",
                "verification_code": otp, "login_device_meta": {"device_name": "Live sports player"}, "phone_number": phone}})
        token = _find_token(response, data)
        if not authenticated_token(token, guest):
            raise AuthError("Hotstar did not confirm account login. Check the OTP or request another one.", "verification_failed", 401)
        return token

    def snapshot(self):
        self.state["cookies"] = [{"name": cookie.name, "value": cookie.value, "domain": cookie.domain,
            "path": cookie.path, "secure": cookie.secure, "expires": cookie.expires} for cookie in self.session.cookies]
        return dict(self.state)


def begin_login(phone, session_path=None):
    phone = normalize_phone(phone)
    destination = session_path or SESSION_PATH
    previous = _read_json(destination) or {}
    age = time.time() - previous.get("created_at", 0) if isinstance(previous.get("created_at", 0), (int, float)) else 60
    if previous.get("phone") == phone and previous.get("otp_confirmed") and 0 <= age < 60:
        raise AuthError(f"Wait {int(60 - age) + 1} seconds before requesting another OTP.", "resend_cooldown", 429)
    client = LoginClient()
    client.guest()
    client.send(phone)
    state = client.snapshot()
    _write_json(session_path or SESSION_PATH, state)
    return state


def pending_login(session_path=None):
    """Resume a confirmed request after restarting the launcher, without resending."""
    state = _read_json(session_path or SESSION_PATH) or {}
    created = state.get("created_at")
    if not state.get("otp_confirmed") or not isinstance(created, (int, float)):
        return {"status": "none"}
    age = time.time() - created
    if age < 0 or age >= SESSION_TTL or not state.get("guest") or not state.get("device_id"):
        return {"status": "none"}
    try:
        phone = normalize_phone(state.get("phone"))
    except AuthError:
        return {"status": "none"}
    return {"status": "pending", "phone": phone,
            "remaining_seconds": int(SESSION_TTL - age), "resend_after": max(0, int(60 - age) + 1)}


def finish_login(phone, otp, session_path=None, token_path=None):
    phone, otp = normalize_phone(phone), normalize_otp(otp)
    path = Path(session_path) if session_path else SESSION_PATH
    state = _read_json(path)
    if not state or not isinstance(state.get("created_at"), (int, float)) or time.time() - state["created_at"] > SESSION_TTL:
        raise AuthError("The OTP session expired. Request another OTP.", "session_expired", 401)
    if state.get("phone") != phone:
        raise AuthError("The phone number changed. Request another OTP.", "phone_mismatch")
    if not state.get("device_id") or not state.get("guest") or state.get("method") not in ("web", "android"):
        raise AuthError("The saved login session is incomplete. Request another OTP.", "session_expired", 401)
    client = LoginClient(state)
    try:
        token = client.verify(phone, otp)
    except AuthError:
        _write_json(path, client.snapshot())
        raise
    save_token_file(token, phone, token_path)
    path.unlink(missing_ok=True)
    return token


_legacy_clients = {}


def api_guest_token():
    client = LoginClient()
    result = client.guest()
    _legacy_clients[result[0]] = client
    return result


def api_send_otp(phone, guest, ps=None, method="web"):
    client = _legacy_clients.get(guest) or LoginClient({"guest": guest, "ps": ps, "method": method,
        "device_id": get_device_id_from_token(guest)})
    _legacy_clients[guest] = client
    return client.send(phone)


def api_verify_otp(phone, otp, guest, ps=None, method="web"):
    client = _legacy_clients.get(guest) or LoginClient({"guest": guest, "ps": ps, "method": method,
        "device_id": get_device_id_from_token(guest), "phone": phone})
    token = client.verify(phone, otp)
    _legacy_clients.pop(guest, None)
    return token
