#!/usr/bin/env python3
"""Preview or apply QQ navigation, backing up configuration before any write."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from henu_plugin.qq_navigation import navigation_config
from henu_plugin.qq_navigation_api import NavigationAPI


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup-dir', type=Path, required=True, help='New, private directory for this attempt')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    args.backup_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    os.umask(0o077)
    with httpx.Client(base_url='https://api.bot.qq.com', timeout=20, follow_redirects=False) as client:
        token_response = client.post('/app/getAppAccessToken', json={
            'appId': os.environ['QQ_APP_ID'], 'clientSecret': os.environ['QQ_CLIENT_SECRET'],
        })
        token_response.raise_for_status()
        token = token_response.json()
        if token.get('code') or not token.get('access_token'):
            raise RuntimeError('QQ access token unavailable')
        client.headers['Authorization'] = 'QQBot ' + token['access_token']
        api = NavigationAPI(client)
        desired = navigation_config()
        snapshot = api.snapshot()
        (args.backup_dir / 'before.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
        (args.backup_dir / 'desired.json').write_text(json.dumps(desired, ensure_ascii=False, indent=2))
        if not args.apply:
            print('Preview saved; no configuration writes.')
            return
        try:
            changes = api.apply(desired, snapshot)
            (args.backup_dir / 'changes.json').write_text(json.dumps(changes, ensure_ascii=False, indent=2))
            after = api.snapshot()
            (args.backup_dir / 'after.json').write_text(json.dumps(after, ensure_ascii=False, indent=2))
            if not api.verify(desired):
                raise RuntimeError('QQ navigation readback mismatch')
            print(json.dumps({'verified': True, 'changes': changes}, ensure_ascii=False))
        except Exception as exc:
            # A timeout can mean a write arrived: do not retry automatically.
            (args.backup_dir / 'attention.json').write_text(json.dumps({'state': 'inspect_before_retry', 'error_type': type(exc).__name__}))
            try:
                after = api.snapshot()
                (args.backup_dir / 'after-failure.json').write_text(json.dumps(after, ensure_ascii=False, indent=2))
            except Exception:
                pass
            raise


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('Navigation configuration stopped: ' + type(exc).__name__ + '; inspect private backup before retry.', file=sys.stderr)
        sys.exit(1)
