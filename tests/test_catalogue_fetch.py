import base64
import json
import time
import unittest
from unittest.mock import patch

from hotstar_catalogue import discover_live_events, CatalogueError, _safe_route


def account_token():
    payload = {'exp': time.time() + 3600, 'sub': json.dumps({'hId': 'test-user', 'isPhoneVerified': True})}
    return 'eyJhbGciOiJIUzI1NiJ9.' + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=') + '.test'


class CatalogueFetchTests(unittest.TestCase):
    def test_requires_account_login_without_reading_provider(self):
        with self.assertRaises(CatalogueError) as raised:
            discover_live_events(None, fetch_page=lambda route: self.fail('No guest catalogue request'))
        self.assertEqual(raised.exception.status, 401)

    def test_pagination_deduplicates_routes_and_matches_without_media_resolution(self):
        calls = []
        event = {'id': 'hs:1', 'hotstarUrl': 'https://www.hotstar.com/in/sports/test/123456789/live'}
        def fetch(route):
            calls.append(route)
            return {'route': route}
        def parse(data):
            if data['route'] == '/v2/pages/sports':
                return [event], ['/v2/widget-type-instance/Live?next=opaque%2Bcursor', '/v2/pages/home']
            return [event], []
        with patch('hotstar_catalogue.parse_catalogue_page', side_effect=parse):
            result = discover_live_events(account_token(), fetch_page=fetch)
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['pages_scanned'], 3)
        self.assertIn('/v2/widget-type-instance/Live?next=opaque%2Bcursor', calls)
        self.assertFalse(result['partial'])

    def test_page_limit_marks_results_partial(self):
        with patch('hotstar_catalogue.parse_catalogue_page', return_value=([], ['/v2/pages/tray_details?next=1'])):
            result = discover_live_events(account_token(), fetch_page=lambda route: {}, max_pages=1)
        self.assertTrue(result['partial'])
        self.assertIn('limit', result['warnings'][0])

    def test_partial_network_failure_keeps_other_page(self):
        def fetch(route):
            if route.endswith('/sports'):
                raise CatalogueError('Catalogue page unavailable.')
            return {}
        with patch('hotstar_catalogue.parse_catalogue_page', return_value=([{'id': 'hs:1'}], [])):
            result = discover_live_events(account_token(), fetch_page=fetch)
        self.assertEqual(result['count'], 1)
        self.assertTrue(result['partial'])

    def test_disallows_external_urls_and_watch_page_resolution(self):
        for route in ['https://example.com/v2/pages/home', '//127.0.0.1/v2/pages/home', '/v2/pages/watch?content_id=1', '/v2/pages/../watch', '/v2/pages/home\n']:
            self.assertFalse(_safe_route(route))


if __name__ == '__main__':
    unittest.main()
