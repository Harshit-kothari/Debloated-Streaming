#!/usr/bin/env python3
"""Private loopback relay managed by the Node launcher (one JSON ready line)."""
import argparse
import contextlib
import importlib.util
import json
import os
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    required = ['flask', 'requests', 'curl_cffi']
    missing = [name for name in required if importlib.util.find_spec(name) is None]
    if args.check:
        print(json.dumps({'python': sys.version.split()[0], 'missing': missing}))
        return 1 if missing else 0
    if missing:
        print(json.dumps({'error': 'Missing Python dependencies: ' + ', '.join(missing)
                          + '. Run python -m pip install -r Requirements.txt'}))
        return 1
    # Imported legacy relay diagnostics belong on stderr, not on the ready channel.
    with contextlib.redirect_stdout(sys.stderr):
        from hotstar_live import app
        from flask import request, jsonify
        from werkzeug.serving import make_server
        secret = os.environ.get('HOTSTAR_SERVICE_TOKEN', '')
        if not secret:
            raise RuntimeError('Start this service through node index.js.')

        @app.before_request
        def private_api():
            if request.path.startswith('/api/'):
                import hmac
                if not hmac.compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + secret):
                    return jsonify(error='This API belongs to the local launcher.'), 403

        @app.get('/api/health')
        def health():
            return jsonify(status='ok', service='debloated-streaming')

        server = make_server('127.0.0.1', args.port, app, threaded=True)
        print(json.dumps({'ready': True, 'port': server.server_port}), file=sys.__stdout__, flush=True)
        # Parent closes stdin on normal exit; also shut down if the launcher dies.
        import threading
        def watch_parent():
            sys.stdin.buffer.read()
            server.shutdown()
        threading.Thread(target=watch_parent, daemon=True).start()
        try:
            server.serve_forever()
        finally:
            server.server_close()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ImportError, RuntimeError) as exc:
        print(json.dumps({'error': str(exc)}), flush=True)
        raise SystemExit(1)
