"""Parse advertised Hotstar live cards and their catalogue continuation links.

This module does not resolve media manifests or perform network requests. A
watch link is emitted only when the provider labels that content as live.
"""
import re
from urllib.parse import parse_qs, urlsplit, urlunsplit


_CID = re.compile(r"^[0-9]{9,12}$")
_LANGUAGES = {"english", "hindi", "tamil", "telugu", "kannada", "malayalam",
              "marathi", "bengali", "gujarati", "punjabi", "odia"}


def _walk(value):
    pending = [(value, 0)]
    seen = set()
    count = 0
    while pending and count < 50000:
        item, depth = pending.pop()
        if not isinstance(item, (dict, list)) or id(item) in seen or depth > 48:
            continue
        seen.add(id(item))
        count += 1
        if isinstance(item, dict):
            yield item
            children = item.values()
        else:
            children = item
        pending.extend((child, depth + 1) for child in reversed(list(children)))


def _text(value, depth=0):
    if depth > 8:
        return ""
    if isinstance(value, str):
        return re.sub(r"[\x00-\x1f\x7f]", " ", value).strip()
    if isinstance(value, dict):
        for key in ("title", "label", "text", "value", "name"):
            found = _text(value.get(key), depth + 1)
            if found:
                return found
    return ""


def _live(card):
    # Some editorial trays attach the LIVE artwork to daily episodes too.
    # Their authoritative watch action still identifies them as VOD episodes.
    types = set()
    for key in ("content_type", "contentType"):
        if isinstance(card.get(key), str):
            types.add(card[key].lower())
    for item in _walk(card):
        nav = item.get("page_navigation")
        if not isinstance(nav, dict) or not isinstance(nav.get("page_url"), str):
            continue
        try:
            query = parse_qs(urlsplit(nav["page_url"]).query)
        except ValueError:
            continue
        types.update(value.lower() for value in query.get("content_type", []))
        for filters in query.get("filters", []):
            types.update(value.lower() for value in re.findall(r"(?:^|[|,&])content_type[=:]([^|,&]+)", filters))
    if types and not any(value == "live" or value.endswith("_live") for value in types):
        return False
    if card.get("is_live") is True or card.get("isLive") is True:
        return True
    if any(_text(card.get(key)).upper() in ("LIVE", "LIVE_NOW", "LIVE NOW")
           for key in ("status", "live_status", "liveStatus")):
        return True
    badge = card.get("live_badge")
    if badge and not (isinstance(badge, dict) and badge.get("is_visible") is False):
        return True
    info = card.get("content_info") or {}
    if not isinstance(info, dict):
        return False
    live_info = info.get("live_info") or {}
    if isinstance(live_info, dict) and _text(live_info.get("live_display_text")).upper() == "LIVE":
        return True
    return info.get("is_live") is True


def _canonical_watch_url(value, content_id):
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value) or "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.username or parsed.password or parsed.port not in (None, 443):
            return None
    except ValueError:
        return None
    if parsed.scheme or parsed.netloc:
        if parsed.scheme != "https" or parsed.hostname not in ("hotstar.com", "www.hotstar.com"):
            return None
        path = parsed.path
    else:
        path = parsed.path
        if path.startswith("/v2/slugs/in/"):
            path = path[len("/v2/slugs"):]
    if not path.startswith("/in/") or content_id not in path.split("/") or "/../" in path:
        return None
    return urlunsplit(("https", "www.hotstar.com", path, "", ""))


def _watch_url(card, content_id):
    for item in _walk(card):
        for key in ("page_slug", "watch_url", "deeplink_url", "content_url", "page_url", "url"):
            result = _canonical_watch_url(item.get(key), content_id)
            if result:
                return result
    return None


def _image(card):
    expanded = card.get("expanded_content_poster") or {}
    candidates = [card.get("image"), card.get("thumbnail"), card.get("hero_img")]
    if isinstance(expanded, dict):
        candidates.append(expanded.get("image"))
    for image in candidates:
        src = image.get("src") if isinstance(image, dict) else image
        if not isinstance(src, str) or not src or "\\" in src:
            continue
        if src.startswith("sources/") and ".." not in src.split("/"):
            return "https://img.hotstar.com/image/upload/f_auto,q_80,w_640/" + src
        try:
            parsed = urlsplit(src)
            if parsed.scheme == "https" and parsed.hostname == "img.hotstar.com" and not parsed.username:
                return src
        except ValueError:
            continue
    return None


def _information(card):
    expanded = card.get("expanded_content_poster") or {}
    info = card.get("content_info")
    if not isinstance(info, dict):
        info = expanded.get("content_info", {}) if isinstance(expanded, dict) else {}
    return info if isinstance(info, dict) else {}


def _event(card):
    cid = str(card.get("content_id") or card.get("contentId") or "")
    if not _CID.fullmatch(cid) or not _live(card):
        return None
    watch = _watch_url(card, cid)
    if not watch:
        return None
    info = _information(card)
    title = (_text(card.get("footer")) or _text(info.get("title"))
             or _text(card.get("title")) or _text(card.get("alt"))
             or f"JioHotstar live {cid}")
    languages = info.get("languages") or []
    labels = [_text(item.get("value")) for item in languages if isinstance(item, dict)]
    labels = list(dict.fromkeys(label for label in labels if label))
    language_names = _LANGUAGES | {label.lower() for label in labels}
    tags = info.get("core_meta_tags") or info.get("tags") or []
    competition = _text(card.get("event_name") or card.get("competition") or info.get("tournament"))
    if not competition:
        competition = next((text for text in (_text(tag) for tag in tags)
                            if text and text.lower() not in language_names
                            and not re.fullmatch(r"\d+\s+languages?", text, re.I)), "")
    return {"id": "hs:" + cid, "provider": "JioHotstar", "isLive": True,
            "title": title, "competition": competition, "poster": _image(card),
            "hotstarUrl": watch, "streamUrl": None, "startTimeMs": 0,
            "language": ", ".join(labels) or None}


def extract_live_events(data):
    """Return one safe canonical watch link per explicitly live content ID."""
    found = {}
    scores = {}
    for card in _walk(data):
        if "content_id" not in card and "contentId" not in card:
            continue
        event = _event(card)
        if not event:
            continue
        key = event["id"]
        score = 10 * bool(card.get("live_badge")) + sum(bool(event[k]) for k in ("poster", "competition", "language"))
        previous = found.get(key)
        if previous is None or score > scores[key]:
            if previous:
                event = {k: value if value is not None and value != "" else previous.get(k)
                         for k, value in event.items()}
            found[key], scores[key] = event, score
        elif previous:
            found[key] = {k: value if value is not None and value != "" else event.get(k)
                          for k, value in previous.items()}
    return list(found.values())


def _provider_route(value):
    if not isinstance(value, str) or not value or "\\" in value or any(ord(c) < 32 for c in value):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/v2/") or ".." in parsed.path.split("/"):
        return None
    if parsed.fragment:
        return None
    return value


def _live_title(value):
    text = " ".join(_text(item.get("title")) for item in _walk(value))
    return bool(re.search(r"\blive\b", text, re.I)) and not bool(re.search(r"\b(?:highlights?|replays?)\b", text, re.I))


def _category_navigation(navigation):
    if not isinstance(navigation, dict) or navigation.get("page_type") == "WatchPage":
        return None
    route = _provider_route(navigation.get("page_url"))
    if not route:
        return None
    path = urlsplit(route).path
    if re.fullmatch(r"/v2/pages/(?:sports(?:_[a-z_]+)?|live(?:_[a-z_]+)?|channels?)", path):
        return route
    if path.startswith(("/v2/slugs/in/sports", "/v2/slugs/in/channels")) and not re.search(r"/[0-9]{9,12}(?:/|$)", path):
        return route
    return None


def extract_catalogue_routes(data):
    """Follow live view-all/pagination and category pages, excluding VOD trays."""
    routes = []

    def add(value):
        route = _provider_route(value)
        if route and route not in routes:
            routes.append(route)

    for item in _walk(data):
        category = _category_navigation(item.get("page_navigation"))
        if category:
            add(category)
        has_items = isinstance(item.get("items"), list)
        if not has_items and not any(key in item for key in ("next_tray_url", "next_page_url", "next_url")):
            continue
        header = {"header": item.get("header"), "tray_header": item.get("tray_header")}
        is_live_group = _live_title(header) or any(_live(card) for card in _walk(item.get("items", []))
                                                   if "content_id" in card or "contentId" in card)
        if not is_live_group:
            continue
        for key in ("next_tray_url", "next_page_url", "next_url"):
            add(item.get(key))
        pagination = item.get("pagination")
        if isinstance(pagination, dict):
            for key in ("next_url", "next_page_url", "next_tray_url"):
                add(pagination.get(key))
        for header_part in _walk(header):
            nav = header_part.get("page_navigation")
            if isinstance(nav, dict) and nav.get("page_type") != "WatchPage":
                add(nav.get("page_url"))
    return routes


def parse_catalogue_page(data):
    return extract_live_events(data), extract_catalogue_routes(data)
