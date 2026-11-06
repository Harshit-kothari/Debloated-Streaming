"""Regression fixtures for local HLS relay; no provider account or network required."""
import json
import time
import unittest
from unittest.mock import patch, Mock

import hotstar_live as live


class Upstream:
    def __init__(self, body=b'', *, url='https://cdn.example/root/master.m3u8', status=200, headers=None):
        self.content = body.encode() if isinstance(body, str) else body
        self.text = self.content.decode('utf-8', errors='replace')
        self.url = url
        self.status_code = status
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, _size):
        yield self.content

    def close(self):
        self.closed = True


class RelayTests(unittest.TestCase):
    def setUp(self):
        live.app.config.update(TESTING=True)
        self.client = live.app.test_client()
        live._sessions.clear()
        live._sessions['fixture'] = {'m3u8_url': 'https://cdn.example/root/master.m3u8?master=1', 'resources': {}, 'resource_ids': {}, 'title': 'Fixture', 'created_at': time.time()}

    def resource(self, path):
        resource_id = path.rsplit('/', 1)[-1]
        return live._sessions['fixture']['resources'][resource_id]

    def test_master_retains_absolute_hosts_paths_query_audio_and_variant(self):
        source = '''#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="hi",URI="https://audio.example/a/hi.m3u8?sig=a%2Fb&n=2"
#EXT-X-STREAM-INF:BANDWIDTH=123,AUDIO="hi"
../video/v1.m3u8?sig=video
'''
        upstream = Upstream(source, url='https://redirect.example/path/master.m3u8?x=1')
        fetcher = Mock()
        fetcher.get.return_value = upstream
        with patch.object(live, '_get_cdn_session', return_value=(fetcher, 'mock')):
            result = self.client.get('/proxy/fixture/master.m3u8')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers.get('Cache-Control'), 'no-cache, no-store, must-revalidate, max-age=0')
        self.assertEqual(result.headers.get('Pragma'), 'no-cache')
        lines = result.text.splitlines()
        audio_path = lines[1].split('URI="')[1].split('"')[0]
        self.assertEqual(self.resource(audio_path), ('https://audio.example/a/hi.m3u8?sig=a%2Fb&n=2', True))
        self.assertEqual(self.resource(lines[3]), ('https://redirect.example/video/v1.m3u8?sig=video', True))
        fetcher.get.assert_called_once_with('https://cdn.example/root/master.m3u8?master=1', headers=live._m3u8_headers(), timeout=20)
        self.assertTrue(upstream.closed)

    def test_master_sorts_variants_highest_quality_first(self):
        source = '''#EXTM3U
#EXT-X-VERSION:3
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",URI="https://audio.example/audio.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=200000,RESOLUTION=320x180,AUDIO="audio"
180p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720,AUDIO="audio"
720p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,AUDIO="audio"
360p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=96000,CODECS="mp4a.40.2"
audio_only.m3u8
'''
        rewritten = live.rewrite_playlist(source, 'https://cdn.example/live/master.m3u8', 'fixture')
        lines = rewritten.splitlines()
        self.assertEqual(lines[0], '#EXTM3U')
        self.assertEqual(lines[1], '#EXT-X-VERSION:3')
        self.assertTrue(lines[2].startswith('#EXT-X-MEDIA:'))
        # First variant must be 720p (highest bandwidth)
        self.assertIn('RESOLUTION=1280x720', lines[3])
        self.assertTrue(lines[4].startswith('/proxy/fixture/resource/'))
        # Second variant must be 360p
        self.assertIn('RESOLUTION=640x360', lines[5])
        self.assertTrue(lines[6].startswith('/proxy/fixture/resource/'))
        # Third variant must be 180p
        self.assertIn('RESOLUTION=320x180', lines[7])
        self.assertTrue(lines[8].startswith('/proxy/fixture/resource/'))
        # Audio-only variant without resolution must be last
        self.assertIn('BANDWIDTH=96000', lines[9])
        self.assertTrue(lines[10].startswith('/proxy/fixture/resource/'))

    def test_media_playlist_rewrites_fmp4_key_and_segments(self):
        body = '''#EXTM3U
#EXT-X-MAP:URI="init.mp4?sig=init"
#EXT-X-KEY:METHOD=AES-128,URI="https://keys.example/key.bin?sig=k"
#EXTINF:4,
segment.m4s?sig=seg%2F1
'''
        rewritten = live.rewrite_playlist(body, 'https://media.example/nested/index.m3u8?sig=index', 'fixture')
        lines = rewritten.splitlines()
        init = lines[1].split('URI="')[1].split('"')[0]
        key = lines[2].split('URI="')[1].split('"')[0]
        self.assertEqual(self.resource(init), ('https://media.example/nested/init.mp4?sig=init', False))
        self.assertEqual(self.resource(key), ('https://keys.example/key.bin?sig=k', False))
        self.assertEqual(self.resource(lines[4]), ('https://media.example/nested/segment.m4s?sig=seg%2F1', False))
        self.assertEqual(live.rewrite_playlist(body, 'https://media.example/nested/index.m3u8?sig=index', 'fixture'), rewritten)

    def test_nested_playlist_uses_registered_url_and_final_redirect_base(self):
        path = live._resource_url('fixture', 'https://audio.example/hi?sig=secret', True)
        fetcher = Mock()
        upstream = Upstream('#EXTM3U\n#EXTINF:4,\npart.aac?auth=a%2Bb\n', url='https://audio2.example/resolved/lang/index.m3u8?token=t')
        fetcher.get.return_value = upstream
        with patch.object(live, '_get_cdn_session', return_value=(fetcher, 'mock')):
            response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(fetcher.get.call_args.args[0], 'https://audio.example/hi?sig=secret')
        self.assertEqual(self.resource(response.text.splitlines()[-1]), ('https://audio2.example/resolved/lang/part.aac?auth=a%2Bb', False))

    def test_master_can_be_media_playlist(self):
        fetcher = Mock()
        fetcher.get.return_value = Upstream('#EXTM3U\n#EXTINF:3,\na.ts?x=2\n')
        with patch.object(live, '_get_cdn_session', return_value=(fetcher, 'mock')):
            result = self.client.get('/proxy/fixture/master.m3u8')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.resource(result.text.splitlines()[-1]), ('https://cdn.example/root/a.ts?x=2', False))

    def test_binary_range_and_content_type_forwarded_and_response_closed(self):
        path = live._resource_url('fixture', 'https://media.example/init.mp4?sig=1')
        upstream = Upstream(b'abc', status=206, headers={'Content-Type': 'video/mp4', 'Content-Length': '3', 'Content-Range': 'bytes 0-2/9', 'Accept-Ranges': 'bytes'})
        fetcher = Mock()
        fetcher.get.return_value = upstream
        with patch.object(live, '_get_cdn_session', return_value=(fetcher, 'mock')):
            result = self.client.get(path, headers={'Range': 'bytes=0-2'}, buffered=True)
        self.assertEqual(result.status_code, 206)
        self.assertEqual(result.data, b'abc')
        self.assertEqual(result.content_type, 'video/mp4')
        self.assertEqual(result.headers['Content-Range'], 'bytes 0-2/9')
        self.assertEqual(fetcher.get.call_args.kwargs['headers']['Range'], 'bytes=0-2')
        self.assertTrue(upstream.closed)

    def test_unknown_session_resources_and_legacy_arbitrary_url_routes_fail(self):
        with patch.object(live, '_get_cdn_session') as fetch:
            for path in ('/proxy/missing/master.m3u8', '/proxy/fixture/resource/missing', '/proxy/fixture/abs/https://example.com', '/proxy/fixture/seg/file.ts'):
                self.assertEqual(self.client.get(path).status_code, 404)
            fetch.assert_not_called()

    def test_drm_uses_official_player_message(self):
        fetcher = Mock()
        upstream = Upstream('#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,KEYFORMAT="com.widevine",URI="https://license.example/"\n')
        fetcher.get.return_value = upstream
        with patch.object(live, '_get_cdn_session', return_value=(fetcher, 'mock')):
            result = self.client.get('/proxy/fixture/master.m3u8')
        self.assertEqual(result.status_code, 422)
        self.assertIn('official JioHotstar player', result.text)
        self.assertTrue(upstream.closed)

    def test_invalid_or_external_content_urls_rejected(self):
        for url in ('https://evil.example/in/shows/foo/1271698292/live', 'https://hotstar.com.evil.example/in/shows/foo/1271698292/live', 'https://www.hotstar.com:bad/in/123456789/live', 'https://www.hotstar.com/in/shows/foo/live', None):
            self.assertEqual(live.extract_slug(url), (None, None))
        self.assertEqual(live.extract_slug('https://www.hotstar.com/in/shows/foo/1271698292/live'), ('in/shows/foo/1271698292', '1271698292'))

    def test_create_requires_login_and_does_not_fetch_provider(self):
        with patch.object(live, 'load_token', return_value=None), patch.object(live, 'fetch_live_urls') as fetch:
            response = self.client.post('/api/create', json={'url': 'https://www.hotstar.com/in/shows/foo/1271698292/live'})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json['code'], 'login_required')
        fetch.assert_not_called()

    def test_newly_saved_token_is_reloaded_and_service_port_used(self):
        live._TOKEN = 'old-token'
        with patch.object(live, 'load_token', return_value='new-token'), patch.object(live.auth, 'authenticated_token', return_value=True), patch.object(live, 'get_device_id_from_token', return_value='device'), patch.object(live, 'fetch_live_urls', return_value='https://cdn.example/master.m3u8') as fetch:
            response = self.client.post('/api/create', json={'url': 'https://www.hotstar.com/in/shows/foo/1271698292/live'}, base_url='http://127.0.0.1:31415')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(live._TOKEN, 'new-token')
        self.assertEqual(fetch.call_args.args[1], 'new-token')
        self.assertTrue(response.json['m3u8_proxy'].startswith('http://127.0.0.1:31415/proxy/'))
        self.assertNotIn('new-token', json.dumps(response.json))

    def test_guest_token_cannot_create_stream_session(self):
        import base64
        payload = {'exp': time.time() + 3600, 'sub': json.dumps({'hId': 'guest-id', 'isPhoneVerified': False})}
        encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b'=').decode()
        guest = 'eyJhbGciOiJIUzI1NiJ9.' + encoded + '.signature'
        with patch.object(live, 'load_token', return_value=guest), patch.object(live, 'fetch_live_urls') as fetch:
            response = self.client.post('/api/create', json={'url': 'https://www.hotstar.com/in/shows/foo/1271698292/live'})
        self.assertEqual(response.status_code, 401)
        fetch.assert_not_called()

    def test_malformed_json_object_returns_client_error(self):
        for route in ('/api/create', '/api/login/send_otp', '/api/login/verify_otp'):
            self.assertEqual(self.client.post(route, json=['bad']).status_code, 400)

    def test_is_mobile_only_token_detection(self):
        import base64
        def make_jwt(sub_dict):
            payload = {'exp': time.time() + 3600, 'sub': json.dumps(sub_dict)}
            encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b'=').decode()
            return 'eyJhbGciOiJIUzI1NiJ9.' + encoded + '.sig'

        mobile_tok = make_jwt({'subscriptions': {'in': {'JHSMobileLite': {'status': 'S'}}}})
        self.assertTrue(live._is_mobile_only_token(mobile_tok))

        super_tok = make_jwt({'subscriptions': {'in': {'HotstarSuper': {'status': 'S'}}}})
        self.assertFalse(live._is_mobile_only_token(super_tok))

        combo_tok = make_jwt({'subscriptions': {'in': {'JHSMobileLite': {'status': 'S'}, 'HotstarPremium': {'status': 'S'}}}})
        self.assertFalse(live._is_mobile_only_token(combo_tok))

        empty_tok = make_jwt({})
        self.assertFalse(live._is_mobile_only_token(empty_tok))
        self.assertFalse(live._is_mobile_only_token(None))

    def test_do_bff_get_omits_accept_encoding_for_cffi(self):
        mock_session = Mock()
        mock_resp = Mock(status_code=200, content=b'{"ok": true}')
        mock_session.get.return_value = mock_resp
        with patch.object(live, '_get_cdn_session', return_value=(mock_session, 'mock_target')):
            headers = {'accept': 'application/json', 'accept-encoding': 'gzip, deflate'}
            resp, used_cffi = live._do_bff_get('https://example.com/api', headers, {}, 'test')
            self.assertTrue(used_cffi)
            self.assertEqual(resp, mock_resp)
            passed_headers = mock_session.get.call_args.kwargs['headers']
            self.assertNotIn('accept-encoding', passed_headers)
            self.assertIn('accept', passed_headers)

    def test_fetch_live_urls_uses_android_profile_for_mobile_token(self):
        import base64
        payload = {'exp': time.time() + 3600, 'sub': json.dumps({'subscriptions': {'in': {'JHSMobileLite': {'status': 'S'}}}})}
        encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b'=').decode()
        mobile_tok = 'eyJhbGciOiJIUzI1NiJ9.' + encoded + '.sig'

        called_platforms = []
        def mock_bff_get(url, hdrs, params, label):
            called_platforms.append(hdrs.get('x-hs-platform'))
            resp = Mock(status_code=200, text='{"success": {}}')
            resp.json.return_value = {
                'success': {
                    'page': {
                        'spaces': {
                            'player': {
                                'widget_wrappers': [{
                                    'widget': {
                                        'data': {
                                            'player_config': {
                                                'media_asset': {
                                                    'primary': {'content_url': 'https://cdn.example/live.m3u8'}
                                                }
                                            }
                                        }
                                    }
                                }]
                            }
                        }
                    }
                }
            }
            return resp, False

        with patch.object(live, 'get_proxystate', return_value=('ps_val', 'ps_ud_val')), patch.object(live, '_do_bff_get', side_effect=mock_bff_get):
            m3u8 = live.fetch_live_urls('in/shows/foo/1271698292', mobile_tok)
            self.assertEqual(m3u8, 'https://cdn.example/live.m3u8')
            self.assertEqual(called_platforms[0], 'android')


if __name__ == '__main__':
    unittest.main()
