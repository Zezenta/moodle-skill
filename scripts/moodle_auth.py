#!/usr/bin/env python3
"""
Moodle authentication script for ESPAM.
Performs SAML2 login flow and persists session cookies to disk.
Run standalone or called by other scripts via moodle_lib.ensure_auth().

Usage:
  python3 moodle_auth.py
"""

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

# Import shared constants and client
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from moodle_lib import (
    MoodleClient, MOODLE_URL
)


def load_skill_env():
    """Load direct environment values, with skill and host .env fallbacks."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    skill_dir = os.path.dirname(base_dir)
    skills_dir = os.path.dirname(skill_dir)
    env_files = [os.path.join(skill_dir, '.env')]
    if os.path.basename(skills_dir) == 'skills':
        env_files.insert(0, os.path.join(os.path.dirname(skills_dir), '.env'))
    values = {}
    for env_file in env_files:
        try:
            with open(env_file, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if line.startswith('export '):
                        line = line[7:].lstrip()
                    if '=' not in line:
                        continue
                    key, value = line.split('=', 1)
                    values[key.strip()] = value.strip().strip('"').strip("'")
        except FileNotFoundError:
            continue
    return values


def authenticate(client):
    """
    Perform the full SAML2 login flow.
    Returns True on success, False on failure.
    On success, cookies and session are persisted via client.save_session().
    """
    print('[1/5] Loading homepage...')
    url, body, status = client._request(f'{MOODLE_URL}/')
    if status != 200:
        print(f'  ERROR: homepage returned {status}')
        return False

    # Find SAML2 login link
    match = re.search(r'href="([^"]*auth/saml2/login[^"]*)"', body)
    if not match:
        print('  ERROR: no SAML2 login link found')
        return False

    saml_url = match.group(1)
    if not saml_url.startswith('http'):
        saml_url = MOODLE_URL + saml_url
    print(f'  Found SAML link')

    print('[2/5] Following SAML2 redirect to STS...')
    url, body, status = client._request(saml_url)
    if 'sts.espam.edu.ec' not in url:
        print(f'  ERROR: did not reach STS. Got: {url[:120]}')
        return False
    print(f'  Reached STS')

    # Load credentials from direct environment variables or the local .env file.
    env_vars = load_skill_env()
    sts_user = os.environ.get('ESPAM_STS_USER') or env_vars.get('ESPAM_STS_USER', '')
    sts_password = os.environ.get('ESPAM_STS_PASSWORD') or env_vars.get('ESPAM_STS_PASSWORD', '')
    if not sts_user or not sts_password:
        print('ERROR: Missing ESPAM_STS_USER / ESPAM_STS_PASSWORD.', file=sys.stderr)
        return False

    print('[3/5] Posting credentials to STS...')
    url, body, status = client._request(url, data={
        'UserName': sts_user,
        'Password': sts_password,
        'AuthMethod': 'FormsAuthentication',
    })
    if status != 200:
        print(f'  ERROR: STS returned {status}')
        return False

    # Extract SAMLResponse and action URL from the form
    saml_resp = re.search(r'name="SAMLResponse"\s+value="([^"]*)"', body)
    relay = re.search(r'name="RelayState"\s+value="([^"]*)"', body)
    action = re.search(r'<form\s+[^>]*action="([^"]*)"', body)

    if not saml_resp:
        print('  ERROR: no SAMLResponse in STS response')
        return False

    acs_url = action.group(1) if action else None
    relay_val = relay.group(1) if relay else ''
    print(f'  Got SAMLResponse ({len(saml_resp.group(1))} chars)')

    print('[4/5] Posting SAMLResponse to Moodle ACS...')
    url, body, status = client._request(acs_url, data={
        'SAMLResponse': saml_resp.group(1),
        'RelayState': relay_val,
    })
    print(f'  Status: {status}')

    if 'MoodleSession' not in client._cookies():
        print('  ERROR: no MoodleSession cookie set')
        return False
    print('  Session established ✓')

    print('[5/5] Loading dashboard for sesskey...')
    url, body, status = client._request(f'{MOODLE_URL}/my/')
    if status != 200:
        print(f'  ERROR: dashboard returned {status}')
        return False

    # Extract M.cfg for sesskey
    cfg_match = re.search(r'M\.cfg\s*=\s*(\{[^;]+\});', body)
    if cfg_match:
        try:
            m_cfg = json.loads(cfg_match.group(1))
            client.sesskey = m_cfg.get('sesskey', '')
            client.userid = m_cfg.get('userId', '')
            print(f'  sesskey: {client.sesskey}')
            print(f'  userId: {client.userid}')
        except json.JSONDecodeError:
            print('  WARNING: could not parse M.cfg')
            return False
    else:
        print('  ERROR: no M.cfg found on dashboard')
        return False

    # Persist cookies and session to disk
    client.save_session()
    print('  Saved cookies and session ✓')

    return True


def main():
    client = MoodleClient()
    success = authenticate(client)
    if success:
        print('\nAuthentication successful.')
        sys.exit(0)
    else:
        print('\nAuthentication failed.')
        sys.exit(1)


if __name__ == '__main__':
    main()
