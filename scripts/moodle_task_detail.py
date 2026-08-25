#!/usr/bin/env python3
"""
Moodle task detail fetcher for ESPAM university.
Fetches and parses the content of a specific task/assignment page.

Requires valid session — runs moodle_auth.py automatically if needed.

Usage:
  python3 moodle_task_detail.py <URL>
  python3 moodle_task_detail.py --url <URL>
  python3 moodle_task_detail.py --url <URL> --json   # Output as JSON
"""

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from moodle_lib import ensure_auth, MOODLE_URL, countdown_str


class TaskPageParser(HTMLParser):
    """Parse a Moodle task/assignment page and extract structured content."""

    SKIP_TITLES = {
        'notificaciones', 'perfilado', 'estado de la entrega', 'footer',
        'contactos', 'calificaciones', 'calendario', 'archivos privados',
        'informes', 'preferencias', 'cerrar sesión', 'página principal',
        'área personal', 'selector', 'nosotros', 'contáctanos',
        'retención', 'móviles', 'busca', 'cursos', 'resumen de',
    }

    def __init__(self):
        super().__init__()
        self.title = ''
        self.description_parts = []
        self.in_description = False
        self.in_title = False
        self.description_depth = 0
        self.files = []
        self.current_file = None
        self.in_submission_status = False
        self.submission_status_parts = []
        self.submission_depth = 0
        self.due_date = ''
        self.grades = ''
        self.due_dt = None
        self.raw_html = ''

        # Track current tag context
        self.tag_stack = []

    def _is_skip_title(self, text):
        """Check if text matches a known non-title pattern."""
        lower = text.lower()
        return any(s in lower for s in self.SKIP_TITLES)

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        self.tag_stack.append((tag, attrs_dict))

        # Title: any element with role="heading"
        class_attr = attrs_dict.get('class', '')
        if attrs_dict.get('role') == 'heading' and not self.title:
            if 'nav-link' not in class_attr and 'breadcrumb' not in class_attr:
                self.in_title = True
                self._title_tag = tag

        # Description region
        id_attr = attrs_dict.get('id', '')

        if self.in_description:
            self.description_depth += 1
        elif 'description' in class_attr.lower() or id_attr == 'intro':
            self.in_description = True
            self.description_depth = 1

        # Submission status
        if self.in_submission_status:
            self.submission_depth += 1
        elif 'submissionstatus' in class_attr.lower() or id_attr == 'submissionstatus':
            self.in_submission_status = True
            self.submission_depth = 1

        # Files and resource links
        href = attrs_dict.get('href', '')
        if tag == 'a' and self.in_description:
            if ('mod_assign' in href or '/pluginfile.php/' in href
                    or '/mod/resource/view.php' in href):
                self.current_file = {'name': '', 'url': href}

    def handle_endtag(self, tag):
        if self.tag_stack:
            self.tag_stack.pop()

        if self.in_title and tag == getattr(self, '_title_tag', 'div'):
            self.in_title = False

        if self.in_description:
            self.description_depth -= 1
            if self.description_depth <= 0:
                self.in_description = False

        if self.in_submission_status:
            self.submission_depth -= 1
            if self.submission_depth <= 0:
                self.in_submission_status = False

        if tag == 'a' and self.current_file:
            if self.current_file['name']:
                self.files.append(self.current_file)
            self.current_file = None

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return

        if self.in_title and not self.title:
            if not self._is_skip_title(text):
                self.title = text

        if self.in_description:
            self.description_parts.append(text)

        if self.in_submission_status:
            self.submission_status_parts.append(text)

        if self.current_file:
            self.current_file['name'] += text

        # Look for due date patterns
        due_match = re.search(r'Fecha de entrega:\s*(.+)', data, re.IGNORECASE)
        if due_match:
            self.due_date = due_match.group(1).strip()
            self._parse_due_datetime(self.due_date)
        due_match2 = re.search(r'Due date:\s*(.+)', data, re.IGNORECASE)
        if due_match2:
            self.due_date = due_match2.group(1).strip()
            self._parse_due_datetime(self.due_date)

        # Look for grade info
        grade_match = re.search(r'Calificación[:\s]+(.+)', data, re.IGNORECASE)
        if grade_match:
            self.grades = grade_match.group(1).strip()

    def _parse_due_datetime(self, text):
        """Try to parse the due date text into a datetime object."""
        # Pattern: "19 de abril de 2025, 23:59" or "19 April 2025, 11:59 PM"
        meses = {
            'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4,
            'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
            'septiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12,
            'january': 1, 'february': 2, 'march': 3, 'april': 4,
            'may': 5, 'june': 6, 'july': 7, 'august': 8,
            'september': 9, 'october': 10, 'november': 11, 'december': 12,
        }
        m = re.search(r'(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})[,\s]+(\d{1,2}):(\d{2})', text, re.IGNORECASE)
        if not m:
            m = re.search(r'(\w+)\s+(\d{1,2}),?\s+(\d{4})[,\s]+(\d{1,2}):(\d{2})', text, re.IGNORECASE)
            if m:
                # Rearrange: month day year hour min
                month_name, day, year, hour, minute = m.groups()
                month = meses.get(month_name.lower())
                if month:
                    try:
                        from moodle_lib import LOCAL_TZ
                        self.due_dt = datetime(int(year), month, int(day), int(hour), int(minute), tzinfo=LOCAL_TZ)
                    except ValueError:
                        pass
                return
        if m:
            day, month_name, year, hour, minute = m.groups()
            month = meses.get(month_name.lower())
            if month:
                try:
                    from moodle_lib import LOCAL_TZ
                    self.due_dt = datetime(int(year), month, int(day), int(hour), int(minute), tzinfo=LOCAL_TZ)
                except ValueError:
                    pass

    def _fallback_title(self, raw_html):
        """Extract title via regex if parser didn't catch it."""
        for m in re.finditer(r'<(?:h[1-6]|div)\b[^>]*>([^<]{15,300})</(?:h[1-6]|div)>', raw_html):
            text = m.group(1).strip()
            if not self._is_skip_title(text) and len(text) > 15:
                return text
        return ''

    def get_result(self, raw_html=''):
        """Return structured task data."""
        if not self.title and raw_html:
            self.title = self._fallback_title(raw_html)

        # Clean description
        desc = ' '.join(self.description_parts).strip()
        desc = re.sub(r'\s+', ' ', desc)

        # Clean submission status
        status = ' '.join(self.submission_status_parts).strip()
        status = re.sub(r'\s+', ' ', status)

        # Deduplicate files by URL
        seen = set()
        unique_files = []
        for f in self.files:
            if f['url'] not in seen:
                seen.add(f['url'])
                unique_files.append(f)

        return {
            'title': self.title,
            'description': desc,
            'due_date': self.due_date,
            'due_dt': self.due_dt.isoformat() if self.due_dt else None,
            'countdown': countdown_str(self.due_dt),
            'submission_status': status,
            'grades': self.grades,
            'files': unique_files,
        }


def fetch_task_detail(client, url):
    """
    Fetch a Moodle task page and parse its content.
    Returns a dict with structured data.
    """
    resp_url, body, status = client._request(url)
    if status != 200:
        return {'error': f'HTTP {status}', 'url': resp_url, 'raw': body[:2000]}

    parser = TaskPageParser()
    parser.raw_html = body
    try:
        parser.feed(body)
    except Exception as e:
        return {
            'error': f'Parse error: {e}',
            'url': resp_url,
            'raw': re.sub(r'<[^>]+>', ' ', body)[:3000].strip(),
        }

    result = parser.get_result(raw_html=body)
    result['url'] = resp_url
    result['status_code'] = status

    # Resolve resource links (/mod/resource/view.php?id=X) to actual file URLs
    resolved_files = []
    for f in result.get('files', []):
        furl = f['url']
        if '/mod/resource/view.php' in furl:
            # Follow the redirect to get the actual pluginfile URL
            resolved_url = _resolve_resource_url(client, furl)
            if resolved_url:
                f['resolved_url'] = resolved_url
                # Try to extract a clean filename
                fname = re.search(r'/([^/?]+\.pdf)', resolved_url, re.I)
                if fname:
                    f['filename'] = urllib.parse.unquote(fname.group(1))
        resolved_files.append(f)
    result['files'] = resolved_files

    # If parser found almost nothing, provide raw HTML as fallback
    if not result['title'] and not result['description']:
        raw_text = re.sub(r'<[^>]+>', ' ', body)
        raw_text = re.sub(r'\s+', ' ', raw_text).strip()
        result['raw'] = raw_text[:5000]

    return result


def _resolve_resource_url(client, resource_url):
    """
    Follow a Moodle resource view URL to its actual file URL (pluginfile.php).
    Returns the resolved URL or None.
    """
    try:
        # Try redirect first (works for direct file resources)
        resp_url, body, status = client._request(resource_url + '&redirect=1')
        if status == 200 and '/pluginfile.php/' in resp_url:
            return resp_url

        # Fallback: scrape the resource page for embedded files
        # Priority 1: iframe src with pluginfile (PDFs, documents shown in viewer)
        iframes = re.findall(r'<iframe[^>]+src="([^"]+)"', body)
        for src in iframes:
            if '/pluginfile.php/' in src and '/mod_resource/content/' in src:
                return src

        # Priority 2: any pluginfile URL from mod_resource content
        pluginfiles = re.findall(
            r'(https?://[^"\s]+pluginfile\.php/\d+/mod_resource/content/[^"\s]+)',
            body
        )
        if pluginfiles:
            return pluginfiles[0]

        # Priority 3: any pluginfile URL (icons, etc.)
        pluginfiles = re.findall(r'(https?://[^"\s]+pluginfile\.php/[^"\s]+)', body)
        if pluginfiles:
            return pluginfiles[0]
    except Exception:
        pass
    return None


def main():
    # Parse args
    url = None
    json_output = '--json' in sys.argv
    download_files = '--download' in sys.argv
    download_dir = None

    for i, a in enumerate(sys.argv[1:]):
        if a == '--url' and i + 2 <= len(sys.argv[1:]):
            url = sys.argv[i + 2]
        elif a == '--download-dir' and i + 2 <= len(sys.argv[1:]):
            download_dir = sys.argv[i + 2]
        elif not a.startswith('-') and not url:
            url = a

    if not url:
        print('Usage: moodle_task_detail.py <URL>', file=sys.stderr)
        print('       moodle_task_detail.py --url <URL> [--json] [--download] [--download-dir DIR]', file=sys.stderr)
        sys.exit(1)

    # Ensure full URL
    if not url.startswith('http'):
        url = MOODLE_URL + url

    # Get authenticated client
    client, ok = ensure_auth()
    if not ok:
        print('ERROR: Could not authenticate.', file=sys.stderr)
        sys.exit(2)

    # Fetch and parse
    result = fetch_task_detail(client, url)

    # Download attached files if requested
    if download_files and result.get('files'):
        download_dir = download_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'downloads')
        os.makedirs(download_dir, exist_ok=True)
        for f in result['files']:
            # Use resolved_url for resources, original url otherwise
            file_url = f.get('resolved_url') or f['url']

            # Prefer filename from resolved_url if available
            filename = f.get('filename', '')
            if not filename:
                filename = os.path.basename(urlparse(file_url).path)
            filename = urllib.parse.unquote(filename)
            dest = os.path.join(download_dir, filename)

            try:
                # Download using the opener directly (handles cookies)
                # Add forcedownload to ensure Moodle serves the raw file
                sep = '&' if '?' in file_url else '?'
                dl_url = file_url + sep + 'forcedownload=1'
                from urllib.request import Request
                req = Request(dl_url)
                resp = client.opener.open(req, timeout=30)
                data = resp.read()
                ctype = resp.headers.get('Content-Type', 'application/octet-stream')
                with open(dest, 'wb') as out:
                    out.write(data)
                print(f'[download] {filename} → {dest} ({ctype}, {len(data)} bytes)', file=sys.stderr)
                f['local_path'] = dest
            except Exception as e:
                print(f'[download] Failed for {filename}: {e}', file=sys.stderr)
                f['download_error'] = str(e)

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
