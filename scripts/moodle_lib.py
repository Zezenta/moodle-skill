#!/usr/bin/env python3
"""
Shared library for Moodle integration scripts.
Handles cookie persistence, session validation, and common utilities.
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from http.cookiejar import LWPCookieJar
from urllib.parse import urlencode
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIE_JAR_PATH = os.path.join(BASE_DIR, 'cookies.lwp')
SESSION_FILE_PATH = os.path.join(BASE_DIR, 'session.json')
MOODLE_URL = 'https://evirtual.espam.edu.ec'
AJAX_URL = f'{MOODLE_URL}/lib/ajax/service.php'
AUTH_SCRIPT = os.path.join(BASE_DIR, 'moodle_auth.py')
TASKS_JSON = os.path.join(BASE_DIR, 'tasks.json')


LOCAL_TZ = timezone(timedelta(hours=-5))


def now_local():
    """Return current datetime in America/Guayaquil."""
    return datetime.now(LOCAL_TZ)


def ts_to_local(ts):
    """Convert Unix timestamp to local datetime string (America/Guayaquil)."""
    if not ts:
        return '?'
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    dt = dt.astimezone(LOCAL_TZ)
    return dt.strftime('%a %d/%m/%Y %H:%M')


def ts_to_local_dt(ts):
    """Convert Unix timestamp to local datetime object (America/Guayaquil)."""
    if not ts:
        return None
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.astimezone(LOCAL_TZ)


def countdown_str(due_dt):
    """
    Return a human-readable countdown string for a due datetime.
    Two levels of granularity:
      > 72h  → 'en X días' or 'hace X días'
      > 24h  → 'faltan Xd Xh' or 'venció hace Xd Xh'
      ≤ 24h  → 'faltan Xh Xm' or 'venció hace Xh Xm'
      past   → 'venció' if beyond a reasonable window
    """
    if not due_dt:
        return ''
    now = now_local()
    diff = due_dt - now
    total_sec = diff.total_seconds()
    past = total_sec < 0
    abs_sec = abs(total_sec)

    days = int(abs_sec // 86400)
    hours = int((abs_sec % 86400) // 3600)
    minutes = int((abs_sec % 3600) // 60)

    if past:
        prefix = 'venció hace'
    else:
        prefix = 'faltan'

    if days >= 3:
        unit = 'día' if days == 1 else 'días'
        return f'{prefix} {days} {unit}'
    elif days >= 1:
        return f'{prefix} {days}d {hours}h'
    else:
        return f'{prefix} {hours}h {minutes}m'


class MoodleClient:
    """Client with persistent cookie-based session."""

    def __init__(self):
        self.cj = LWPCookieJar(COOKIE_JAR_PATH)
        # Load existing cookies if the file exists
        if os.path.exists(COOKIE_JAR_PATH):
            try:
                self.cj.load(ignore_discard=True, ignore_expires=True)
            except Exception:
                pass
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj)
        )
        self.sesskey = None
        self.userid = None
        self._load_session()

    def _load_session(self):
        """Load sesskey and userid from session.json."""
        if os.path.exists(SESSION_FILE_PATH):
            try:
                with open(SESSION_FILE_PATH) as f:
                    data = json.load(f)
                self.sesskey = data.get('sesskey')
                self.userid = data.get('userid')
            except (json.JSONDecodeError, IOError):
                pass

    def _request(self, url, data=None, headers=None):
        """Make HTTP request, follow redirects, return (response_url, body, status)."""
        req_headers = headers or {}
        req_data = None

        if data is not None:
            if isinstance(data, dict):
                req_data = urlencode(data).encode()
                req_headers.setdefault('Content-Type', 'application/x-www-form-urlencoded')
            elif isinstance(data, str):
                req_data = data.encode()

        req = urllib.request.Request(url, data=req_data, headers=req_headers)

        try:
            resp = self.opener.open(req, timeout=20)
            body = resp.read().decode('utf-8', errors='replace')
            return resp.url, body, resp.status
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='replace')
            return getattr(e, 'url', url), body, e.code

    def _download_file(self, url, dest_path=None):
        """Download a file from Moodle (pluginfile.php).

        Strips forcedownload param so Moodle returns the raw content
        instead of a Content-Disposition: attachment response.

        Args:
            url: Pluginfile URL (may include forcedownload param)
            dest_path: If set, save binary to this path. Otherwise return bytes.

        Returns:
            (bytes, content_type) if dest_path is None
            (dest_path, content_type) if dest_path is set
        """
        from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, quote

        # Encode path component to handle spaces and special chars (Python 3.14+ rejects unencoded URLs)
        parsed = urlparse(url)
        encoded_path = quote(parsed.path, safe='/')

        # Strip forcedownload to get inline content
        params = parse_qs(parsed.query)
        params.pop('forcedownload', None)
        clean_url = urlunparse(parsed._replace(path=encoded_path, query=urlencode(params, doseq=True)))

        req = urllib.request.Request(clean_url)
        resp = self.opener.open(req, timeout=30)
        data = resp.read()
        content_type = resp.headers.get('Content-Type', 'application/octet-stream')

        if dest_path:
            os.makedirs(os.path.dirname(dest_path) or '.', exist_ok=True)
            with open(dest_path, 'wb') as f:
                f.write(data)
            return dest_path, content_type
        return data, content_type

    def _cookies(self):
        return {c.name: c.value for c in self.cj}

    def is_session_valid(self):
        """Check if the current session is still valid with a lightweight request."""
        if not self.sesskey or 'MoodleSession' not in self._cookies():
            return False
        try:
            # Fetch the dashboard — if session is valid, it loads without redirect to login
            url, body, status = self._request(f'{MOODLE_URL}/my/')
            if status != 200:
                return False
            # If we get redirected to login page, session is dead
            if 'login' in url.lower() and 'my' not in url.lower():
                return False
            # Verify sesskey is still present in the page
            if self.sesskey not in body:
                return False
            return True
        except Exception:
            return False

    def ajax(self, methodname, args=None):
        """Call a Moodle AJAX endpoint. Returns parsed JSON."""
        if args is None:
            args = {}
        url = f'{AJAX_URL}?sesskey={self.sesskey}&info={methodname}'
        payload = json.dumps([{
            'index': 0,
            'methodname': methodname,
            'args': args,
        }])
        req = urllib.request.Request(url, data=payload.encode(), headers={
            'Content-Type': 'application/json',
        })
        try:
            resp = self.opener.open(req, timeout=20)
            body = resp.read().decode('utf-8', errors='replace')
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='replace')
            print(f'  AJAX HTTP error {e.code}: {body[:200]}', file=sys.stderr)
            return None
        return json.loads(body)

    def save_session(self):
        """Persist cookies and session data to disk."""
        try:
            self.cj.save(ignore_discard=True, ignore_expires=True)
        except Exception as e:
            print(f'Warning: could not save cookies: {e}', file=sys.stderr)

        local_tz = timezone(timedelta(hours=-5))
        data = {
            'sesskey': self.sesskey,
            'userid': self.userid,
            'saved_at': datetime.now(local_tz).isoformat(),
        }
        try:
            with open(SESSION_FILE_PATH, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f'Warning: could not save session: {e}', file=sys.stderr)


def ensure_auth():
    """
    Get a valid authenticated MoodleClient.
    Runs moodle_auth.py if needed.
    Returns (client, success_bool).
    """
    client = MoodleClient()

    if client.is_session_valid():
        return client, True

    print('[auth] No valid session, authenticating...', file=sys.stderr)
    result = subprocess.run(
        [sys.executable, AUTH_SCRIPT],
        capture_output=True, text=True, timeout=60,
    )

    if result.returncode != 0:
        print(f'[auth] Authentication failed: {result.stderr.strip()}', file=sys.stderr)
        return client, False

    # Reload cookies and session
    client = MoodleClient()
    if client.is_session_valid():
        return client, True

    print('[auth] Auth succeeded but session validation failed', file=sys.stderr)
    return client, False
