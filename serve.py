#!/usr/bin/env python3
"""
serve.py — run iRacing Season Planner from this folder with real autosave.

    python3 serve.py

Opens http://127.0.0.1:8765/ in your browser. Changes are written to
planner-settings.json next to this script, so the folder holds the state.

Nothing leaves your machine: the server binds to 127.0.0.1 and only ever
reads and writes files in this one folder.

Files it expects beside it:
    index.html                  the app
    season.json                 a season from season_to_json.py
    planner-settings.json       created on first save
"""

import argparse
import datetime
import http.server
import json
import os
import re
import socketserver
import sys
import threading
import webbrowser

DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(DIR, 'planner-settings.json')
API = '/planner-api'
MAX_BODY = 2 * 1024 * 1024


def read_settings():
    try:
        with open(SETTINGS, encoding='utf-8') as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as exc:
        print(f'  ! {os.path.basename(SETTINGS)} is unreadable ({exc}); starting fresh')
        return None


_last_written = None


def write_settings(obj):
    """Write the file IN PLACE"""
    global _last_written
    text = json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True)
    if text == _last_written and os.path.exists(SETTINGS):
        return False                      # nothing changed; don't touch the folder
    with open(SETTINGS, 'w', encoding='utf-8') as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    _last_written = text
    return True


DUP_RE = re.compile(r'^planner-settings[ (]\d+\)?\.json$')


def _copy_number(name):
    m = re.search(r'[ (](\d+)\)?\.json$', name)
    return int(m.group(1)) if m else 0


def find_duplicates():
    """Oldest first. Sync clients bump the number as they go, so that breaks
    ties when several copies land within the same second."""
    return sorted((f for f in os.listdir(DIR) if DUP_RE.match(f)),
                  key=lambda f: (os.path.getmtime(os.path.join(DIR, f)), _copy_number(f)))


def describe(path):
    """What is actually in a settings file, for choosing between conflict copies."""
    try:
        with open(path, encoding='utf-8') as fh:
            obj = json.load(fh)
        if not isinstance(obj, dict):
            return None, 'not a settings object'
        return obj, '{} cars \u00b7 {} tracks \u00b7 {} favourites'.format(
            len(obj.get('cars') or []), len(obj.get('tracks') or []), len(obj.get('favs') or []))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        return None, 'unreadable ({})'.format(type(exc).__name__)


def tidy():
    """Fold sync-conflict copies back into one file."""
    dups = find_duplicates()
    if not dups:
        print('No duplicate settings files here — nothing to tidy.')
        return
    print(f'Found {len(dups)} duplicate settings files, oldest first:')
    newest = None
    for f in dups:
        full = os.path.join(DIR, f)
        obj, what = describe(full)
        when = datetime.datetime.fromtimestamp(os.path.getmtime(full)).strftime('%d %b %H:%M')
        print(f'  {f:30} {when}   {what}')
        if obj is not None:
            newest = f
    have_main = os.path.exists(SETTINGS)
    if have_main:
        _, what = describe(SETTINGS)
        print(f'\nKeeping planner-settings.json ({what}); deleting all {len(dups)} duplicates.')
    elif newest:
        print('\nplanner-settings.json is missing. Promoting the newest readable copy:')
        print(f'  {newest}  ->  planner-settings.json, then deleting the other {len(dups)-1}.')
        print('  If a different one above looks right, rename that one to'
              ' planner-settings.json yourself and run this again.')
    else:
        print('\nNone of them contain readable JSON. Delete them all?')
    if input('Proceed? [y/N] ').strip().lower() not in ('y', 'yes'):
        print('Left everything alone.')
        return
    if not have_main and newest:
        os.replace(os.path.join(DIR, newest), SETTINGS)
        dups.remove(newest)
    for f in dups:
        os.remove(os.path.join(DIR, f))
    print(f'Done. {len(dups)} file(s) removed.')


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=DIR, **kw)

    def _json(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _is_api(self):
        return self.path.split('?')[0].rstrip('/').endswith(API.lstrip('/'))

    def do_GET(self):
        if self._is_api():
            return self._json({'ok': True, 'settings': read_settings()})
        return super().do_GET()

    def do_PUT(self):
        if not self._is_api():
            return self.send_error(405, 'Only planner-api accepts writes')
        try:
            length = int(self.headers.get('Content-Length', 0))
        except ValueError:
            return self._json({'ok': False, 'error': 'bad length'}, 400)
        if length <= 0 or length > MAX_BODY:
            return self._json({'ok': False, 'error': 'bad length'}, 400)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError('expected an object')
        except (ValueError, UnicodeDecodeError) as exc:
            return self._json({'ok': False, 'error': str(exc)}, 400)
        try:
            wrote = write_settings(payload)
        except OSError as exc:
            print(f'  ! could not write settings: {exc}')
            return self._json({'ok': False, 'error': str(exc)}, 500)
        if wrote:
            print('  saved ' + os.path.basename(SETTINGS))
        return self._json({'ok': True})

    def log_message(self, fmt, *args):
        pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--no-browser', action='store_true')
    ap.add_argument('--tidy', action='store_true',
                    help='fold sync-conflict copies (planner-settings 2.json, …) back into one file')
    args = ap.parse_args()

    if args.tidy:
        return tidy()

    app = next((f for f in os.listdir(DIR) if f.endswith('.html')), None)
    if not app:
        sys.exit(f'No .html file in {DIR} — put the planner here first.')

    url = f'http://127.0.0.1:{args.port}/{app.replace(" ", "%20")}'
    try:
        srv = Server(('127.0.0.1', args.port), Handler)
    except OSError as exc:
        sys.exit(f'Port {args.port} is busy ({exc}). Try: python3 serve.py --port 9000')

    dups = find_duplicates()
    if dups:
        print(f'! {len(dups)} duplicate settings files here'
              + ('' if os.path.exists(SETTINGS) else ', and planner-settings.json is missing'))
        print('  Run: python3 serve.py --tidy\n')

    print(f'Serving {DIR}')
    print(f'  {url}')
    print(f'  settings: {SETTINGS}')
    print('  Ctrl-C to stop')
    if not args.no_browser:
        threading.Timer(0.6, webbrowser.open, args=(url,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')
    finally:
        srv.server_close()


if __name__ == '__main__':
    main()
