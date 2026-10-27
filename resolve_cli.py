#!/usr/bin/env python3
"""Resolve a Hotstar URL to a local relay URL; keep running during playback."""
import argparse
import contextlib
import json
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resolve', required=True, metavar='URL')
    parser.add_argument('--lang', default='eng')
    args = parser.parse_args()
    with contextlib.redirect_stdout(sys.stderr):
        from hotstar_live import app
        from werkzeug.serving import make_server
        server = make_server('127.0.0.1', 0, app, threaded=True)
        base = f'http://127.0.0.1:{server.server_port}'
        with app.test_client() as client:
            response = client.post('/api/create', json={'url': args.resolve, 'lang': args.lang}, base_url=base)
        data = response.get_json()
        if response.status_code != 200:
            server.server_close()
            print(json.dumps(data), file=sys.__stdout__, flush=True)
            return 1
        print(json.dumps({'streamUrl': data['m3u8_proxy'], 'watchUrl': data['watch_link']}), file=sys.__stdout__, flush=True)
        print('Keep this process running while the player uses the relay. Ctrl+C stops it.', file=sys.stderr)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
