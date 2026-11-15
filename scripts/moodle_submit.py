#!/usr/bin/env python3
"""
Upload files to a Moodle assignment submission (ESPAM campus).

Flow (reverse-engineered from the web UI, Nov 2026):
  1. GET  /mod/assign/view.php?id=CMID&action=editsubmission
       -> hidden form fields (lastmodified, userid, files_filemanager = draft itemid),
          filepicker options (client_id, contextid, upload repo id, maxbytes, maxfiles)
  2. POST /repository/repository_ajax.php?action=upload   (multipart, field repo_upload_file)
       -> file lands in the user's draft area (itemid)
  3. POST /repository/draftfiles_ajax.php?action=list      -> confirm draft contents
  4. POST /mod/assign/view.php action=savesubmission files_filemanager=<itemid>  (303 on success)
  Remove: POST /mod/assign/view.php action=removesubmission (id, userid, sesskey)

Usage:
  python3 moodle_submit.py <assign URL or cmid> FILE [FILE...]            # upload + save submission
  python3 moodle_submit.py <assign URL or cmid> FILE --draft-only         # upload to draft area only (nothing submitted)
  python3 moodle_submit.py <assign URL or cmid> FILE --replace            # delete existing files first (when maxfiles is reached)
  python3 moodle_submit.py <assign URL or cmid> --status                  # show submission status only
  python3 moodle_submit.py <assign URL or cmid> --remove                  # remove the current submission

Saving a submission is visible to the teacher. Only run without --draft-only when the user asked to submit.
"""
import argparse
import html
import json
import mimetypes
import os
import re
import sys
import uuid
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from moodle_lib import ensure_auth, MOODLE_URL  # noqa: E402


def cmid_of(arg):
    m = re.search(r'[?&]id=(\d+)', arg)
    if m:
        return m.group(1)
    if arg.isdigit():
        return arg
    sys.exit(f'Not an assignment URL or cmid: {arg}')


def raw(client, url, body=None, headers=None):
    req = urllib.request.Request(url, data=body, headers=headers or {})
    try:
        resp = client.opener.open(req, timeout=60)
        return resp.status, resp.read().decode('utf-8', 'replace'), resp.url
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace'), url


def multipart(fields, file_field, path):
    boundary = '----hermes' + uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    name = os.path.basename(path)
    ctype = mimetypes.guess_type(name)[0] or 'application/octet-stream'
    with open(path, 'rb') as f:
        data = f.read()
    out.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{name}"\r\n'
               f'Content-Type: {ctype}\r\n\r\n'.encode() + data + b'\r\n')
    out.append(f'--{boundary}--\r\n'.encode())
    return b''.join(out), f'multipart/form-data; boundary={boundary}'


def form_fields(body):
    f = re.search(r'<form[^>]*class="[^"]*mform[^"]*"[^>]*>.*?</form>', body, re.S)
    if not f:
        return None
    fields = {}
    for m in re.finditer(r'<input[^>]*>', f.group(0)):
        tag = m.group(0)
        name = re.search(r'name="([^"]+)"', tag)
        typ = (re.search(r'type="([^"]+)"', tag) or [None, 'text'])[1]
        if not name or typ in ('submit', 'button', 'file'):
            continue
        if typ in ('checkbox', 'radio') and 'checked' not in tag:
            continue
        fields[name.group(1)] = html.unescape((re.search(r'value="([^"]*)"', tag) or [None, ''])[1])
    for m in re.finditer(r'<textarea[^>]*name="([^"]+)"[^>]*>(.*?)</textarea>', f.group(0), re.S):
        fields[m.group(1)] = html.unescape(m.group(2))
    return fields


def picker_opts(body):
    def grab(k):
        m = re.search(rf'"{k}":"?([^",}}]+)"?', body)
        return m.group(1) if m else None
    upload_repo = None
    for rid, rtype in re.findall(r'"id":"?(\d+)"?,"name":"[^"]+","type":"([^"]+)"', body):
        if rtype == 'upload':
            upload_repo = rid
            break
    return {'client_id': grab('client_id'), 'contextid': grab('contextid'), 'repo_id': upload_repo,
            'maxbytes': grab('maxbytes') or '-1', 'areamaxbytes': grab('areamaxbytes') or '-1',
            'maxfiles': grab('maxfiles')}


def draft_list(client, itemid, client_id):
    st, body, _ = raw(client, f'{MOODLE_URL}/repository/draftfiles_ajax.php?action=list',
                      json_form({'sesskey': client.sesskey, 'client_id': client_id, 'filepath': '/', 'itemid': itemid}),
                      {'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        return [f['filename'] for f in json.loads(body).get('list', [])]
    except ValueError:
        return None


def json_form(d):
    from urllib.parse import urlencode
    return urlencode(d).encode()


def status(client, cmid):
    _, body, _ = raw(client, f'{MOODLE_URL}/mod/assign/view.php?id={cmid}')
    rows = {}
    for th, td in re.findall(r'<tr[^>]*>\s*<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>', body, re.S):
        k = re.sub(r'<[^>]+>|\s+', ' ', th).strip()
        v = re.sub(r'<[^>]+>|\s+', ' ', html.unescape(td)).strip()
        if k and '___' not in v:
            rows[k] = v[:200]
    title = re.search(r'<title>(.*?)</title>', body, re.S)
    rows['_title'] = html.unescape(title.group(1).strip()) if title else ''
    rows['_needs_submit_for_grading'] = 'action=submit' in body and 'submit' in body
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('assignment')
    ap.add_argument('files', nargs='*')
    ap.add_argument('--draft-only', action='store_true')
    ap.add_argument('--replace', action='store_true')
    ap.add_argument('--status', action='store_true')
    ap.add_argument('--remove', action='store_true')
    a = ap.parse_args()

    client, ok = ensure_auth()
    if not ok:
        sys.exit('ERROR: could not authenticate to Moodle')
    cmid = cmid_of(a.assignment)

    if a.status:
        print(json.dumps(status(client, cmid), ensure_ascii=False, indent=1))
        return
    if a.remove:
        st, _, url = raw(client, f'{MOODLE_URL}/mod/assign/view.php', json_form(
            {'id': cmid, 'action': 'removesubmission', 'userid': client.userid, 'sesskey': client.sesskey}),
            {'Content-Type': 'application/x-www-form-urlencoded'})
        print(json.dumps({'removed': st in (200, 303), 'http': st, 'status': status(client, cmid)}, ensure_ascii=False, indent=1))
        return
    if not a.files:
        sys.exit('No files given')
    for f in a.files:
        if not os.path.isfile(f) or os.path.getsize(f) == 0:
            sys.exit(f'File missing or empty: {f}')

    _, body, _ = raw(client, f'{MOODLE_URL}/mod/assign/view.php?id={cmid}&action=editsubmission')
    fields = form_fields(body)
    if not fields or 'files_filemanager' not in fields:
        sys.exit('ERROR: no file submission form (assignment closed, already submitted for grading, or no file submissions)')
    opts = picker_opts(body)
    itemid = fields['files_filemanager']
    existing = draft_list(client, itemid, opts['client_id']) or []

    maxfiles = int(opts['maxfiles']) if opts['maxfiles'] and opts['maxfiles'].lstrip('-').isdigit() else -1
    if maxfiles > 0 and len(existing) + len(a.files) > maxfiles:
        if not a.replace:
            sys.exit(f'ERROR: assignment allows {maxfiles} file(s) and already has {existing}. Use --replace.')
        for name in existing:
            raw(client, f'{MOODLE_URL}/repository/draftfiles_ajax.php?action=delete', json_form(
                {'sesskey': client.sesskey, 'client_id': opts['client_id'], 'filepath': '/', 'itemid': itemid,
                 'filename': name}), {'Content-Type': 'application/x-www-form-urlencoded'})
    for path in a.files:
        if int(opts['maxbytes']) > 0 and os.path.getsize(path) > int(opts['maxbytes']):
            sys.exit(f'ERROR: {path} exceeds maxbytes {opts["maxbytes"]}')

    uploaded = []
    for path in a.files:
        data, ctype = multipart({
            'title': os.path.basename(path), 'author': '', 'license': 'unknown', 'itemid': itemid,
            'repo_id': opts['repo_id'], 'p': '', 'page': '', 'env': 'filemanager', 'sesskey': client.sesskey,
            'client_id': opts['client_id'], 'maxbytes': opts['maxbytes'], 'areamaxbytes': opts['areamaxbytes'],
            'ctx_id': opts['contextid'], 'savepath': '/'}, 'repo_upload_file', path)
        st, resp, _ = raw(client, f'{MOODLE_URL}/repository/repository_ajax.php?action=upload', data,
                          {'Content-Type': ctype})
        try:
            r = json.loads(resp)
        except ValueError:
            sys.exit(f'ERROR: upload returned non-JSON (HTTP {st}): {resp[:200]}')
        if 'error' in r:
            sys.exit(f'ERROR uploading {path}: {r["error"]}')
        uploaded.append(r.get('file'))

    draft = draft_list(client, itemid, opts['client_id'])
    result = {'assignment': cmid, 'itemid': itemid, 'uploaded': uploaded, 'draft_files': draft}
    if a.draft_only:
        result['saved'] = False
        result['note'] = 'Draft only: nothing was submitted. Draft files expire on their own.'
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return

    fields['action'] = 'savesubmission'
    fields['submitbutton'] = 'Guardar cambios'
    st, resp, url = raw(client, f'{MOODLE_URL}/mod/assign/view.php', json_form(fields),
                        {'Content-Type': 'application/x-www-form-urlencoded'})
    result['saved'] = st in (200, 303) and 'action=editsubmission' not in url
    result['http'] = st
    result['status'] = status(client, cmid)
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
