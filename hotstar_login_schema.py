"""Interpret login response shapes without treating HTTP 200 as OTP delivery.

Returned provider errors are internal, unsanitized strings. The caller must
redact credentials and personal data before showing or recording them.
"""
from urllib.parse import parse_qs, urlsplit

from hotstar_proto import ProtoDecodeError, decode_message


_NO_ERROR = {"", "0", "false", "none", "null", "ok", "success", "no_error", "err_none"}


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _code(value):
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        result = str(value).strip()
        return result if result.lower() not in _NO_ERROR else ""
    return ""


def _format_error(code="", message=""):
    code, message = _code(code), _text(message)
    if code and message and code not in message:
        return f"{code}: {message}"
    return message or code or None


def _walk(data):
    """Walk JSON containers with a finite budget, including lists of widgets."""
    pending = [(data, 0)]
    visited = set()
    count = 0
    while pending and count < 4096:
        value, depth = pending.pop()
        if not isinstance(value, (dict, list)) or id(value) in visited or depth > 32:
            continue
        visited.add(id(value))
        count += 1
        if isinstance(value, dict):
            yield value
            children = value.values()
        else:
            children = value
        pending.extend((child, depth + 1) for child in reversed(list(children)))


def _error_envelope(value, depth=0):
    if depth > 8:
        return None
    if isinstance(value, str):
        return value.strip() if value.strip().lower() not in _NO_ERROR else None
    if isinstance(value, list):
        for item in value[:128]:
            result = _error_envelope(item, depth + 1)
            if result:
                return result
        return None
    if isinstance(value, dict):
        return _format_error(value.get("error_code") or value.get("errorCode") or value.get("code"),
                             value.get("error_message") or value.get("errorMessage")
                             or value.get("message") or value.get("description") or value.get("reason"))
    return "Request rejected" if value is True else None


def provider_error(data):
    """Return a top-level or nested provider error, preserving its code/message."""
    for item in _walk(data):
        for key in ("error", "errors"):
            if key in item:
                problem = _error_envelope(item[key])
                if problem:
                    return problem
        problem = _format_error(item.get("error_code") or item.get("errorCode"),
                                item.get("error_message") or item.get("errorMessage"))
        if problem:
            return problem
        if item.get("success") is False or _text(item.get("status")).lower() in ("error", "failed", "failure"):
            return _format_error(item.get("code"), item.get("message") or item.get("description")) or "Request rejected"
    return None


def binary_error(content):
    """Read the protobuf response envelope's field-2 error, not arbitrary text.

    The error message uses field 1 for its code and field 2 for its message.
    Other envelope fields are not interpreted as errors.
    """
    try:
        envelope = decode_message(content)
        for field in envelope:
            if field["field"] != 2 or field["wire"] != 2:
                continue
            parts = decode_message(field["value"])
            values = {}
            for part in parts:
                if part["field"] not in (1, 2):
                    continue
                if part["wire"] == 2:
                    try:
                        values[part["field"]] = part["value"].decode("utf-8").strip()
                    except UnicodeDecodeError:
                        continue
                elif part["field"] == 1 and part["wire"] == 0:
                    values[1] = part["value"]
            problem = _format_error(values.get(1), values.get(2))
            if problem:
                return problem
    except (ProtoDecodeError, TypeError):
        return None
    return None


def _verify_template(value):
    value = _text(value)
    return value == "VerifyOtpWidget" or value.endswith((".VerifyOtpWidget", "/VerifyOtpWidget"))


def otp_confirmed(data):
    """Confirm an OTP request only when the server advances to verification."""
    if provider_error(data):
        return False
    for item in _walk(data):
        if item.get("otp_sent") is True or _text(item.get("status")).upper() == "OTP_SENT":
            return True
        if _verify_template(item.get("@type")) or _verify_template(item.get("template")):
            return True
        template = item.get("template")
        if isinstance(template, dict) and _verify_template(template.get("name")):
            return True
    return False


def find_widget_action(data, action_name):
    """Find a relative fetch_widget URL with one exact sendOtp/verifyOtp action."""
    if action_name not in ("sendOtp", "verifyOtp"):
        return None
    for item in _walk(data):
        widget = item.get("fetch_widget")
        url = widget.get("url") if isinstance(widget, dict) else None
        if not isinstance(url, str) or not url or any(ord(char) < 32 or ord(char) == 127 for char in url) or "\\" in url:
            continue
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        if parsed.scheme or parsed.netloc or not parsed.path or url.startswith("//"):
            continue
        if parse_qs(parsed.query).get("action") == [action_name]:
            return url
    return None
