"""Read live event cards from the signed-in Hotstar catalogue."""
import copy
import hashlib
import threading
import time
from urllib.parse import urlsplit

import requests

from hotstar_catalogue_parser import parse_catalogue_page
from hotstar_auth import authenticated_token, get_device_id_from_token, _provider_error


class CatalogueError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


_cache = {}
_cache_lock = threading.Lock()
_ROOTS = ['/v2/pages/sports', '/v2/pages/home']


def _safe_route(route):
    if not isinstance(route, str):
        return False
    try:
        parsed = urlsplit(route)
    except (ValueError, TypeError):
        return False
    return (not parsed.scheme and not parsed.netloc and not parsed.fragment
            and parsed.path.startswith(('/v2/pages/', '/v2/slugs/', '/v2/widget-type-instance/'))
            and parsed.path != '/v2/pages/watch'
            and '..' not in parsed.path.split('/')
            and not any(ord(char) < 32 for char in route) and '\\' not in route)


def _read_page(session, route, headers, timeout):
    if not _safe_route(route):
        raise CatalogueError('Hotstar returned an invalid catalogue navigation link.')
    response = None
    try:
        response = session.get('https://www.hotstar.com/api/internal/bff' + route,
                               headers=headers, timeout=timeout)
        if response.status_code in (401, 403):
            raise CatalogueError('Hotstar rejected catalogue access. Check your login and account availability.', 401)
        if response.status_code != 200:
            raise CatalogueError(f'Hotstar catalogue returned HTTP {response.status_code}.')
        try:
            data = response.json()
        except ValueError:
            raise CatalogueError('Hotstar did not return a readable catalogue. Try again later.') from None
        if not isinstance(data, dict):
            raise CatalogueError('Hotstar returned an unexpected catalogue response.')
        problem = _provider_error(data)
        if problem:
            raise CatalogueError('Hotstar catalogue: ' + problem)
        return data
    except requests.RequestException:
        raise CatalogueError('Could not reach the Hotstar catalogue. Check your connection.') from None
    finally:
        if response is not None:
            response.close()


def discover_live_events(token, *, refresh=False, fetch_page=None, max_pages=24, budget_seconds=35):
    """Follow live trays and their server-provided cursors; never resolve media here.

    Limits and unavailable pages are reported as partial results. Catalogue
    visibility is determined by Hotstar for this account and region.
    """
    if not authenticated_token(token):
        raise CatalogueError('Log in to JioHotstar to discover its live events.', 401)
    key = hashlib.sha256(token.encode()).hexdigest()
    with _cache_lock:
        cached = _cache.get(key)
        if fetch_page is None and not refresh and cached and time.monotonic() - cached[0] < 60:
            return copy.deepcopy(cached[1])
    # Lazy import avoids a circular dependency with the Flask app and its headers.
    from hotstar_live import _dl_web_hdrs
    headers = _dl_web_hdrs(token=token)
    headers['x-hs-device-id'] = get_device_id_from_token(token)
    headers['x-hs-accept-language'] = 'eng'
    session = requests.Session()
    deadline = time.monotonic() + budget_seconds
    queue = list(_ROOTS)
    visited = set()
    matches = {}
    warnings = []
    successful = 0
    try:
        while queue and len(visited) < max_pages and time.monotonic() < deadline:
            route = queue.pop(0)
            if route in visited or not _safe_route(route):
                continue
            visited.add(route)
            try:
                remaining = max(0.1, min(10, deadline - time.monotonic()))
                data = fetch_page(route) if fetch_page else _read_page(session, route, headers, remaining)
                events, routes = parse_catalogue_page(data)
                successful += 1
                for event in events:
                    matches.setdefault(event['id'], event)
                for next_route in routes:
                    if _safe_route(next_route) and next_route not in visited and next_route not in queue:
                        queue.append(next_route)
            except CatalogueError as exc:
                if exc.status == 401:
                    raise
                warnings.append(str(exc))
        if queue:
            warnings.append('Hotstar discovery reached its page or time limit; the live list may be incomplete.')
        if not successful:
            raise CatalogueError(warnings[0] if warnings else 'Hotstar returned no readable catalogue pages.')
        result = {'matches': list(matches.values()), 'warnings': list(dict.fromkeys(warnings)),
                  'partial': bool(warnings), 'pages_scanned': len(visited), 'count': len(matches)}
        if fetch_page is None:
            with _cache_lock:
                # Keep only this recent session; account tokens never reach the UI.
                _cache.clear()
                _cache[key] = (time.monotonic(), copy.deepcopy(result))
        return result
    finally:
        session.close()
