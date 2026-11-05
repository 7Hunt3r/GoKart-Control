#!/usr/bin/env python3
"""Generate unique local maintenance credentials. Never prints secrets."""
from pathlib import Path
import json
import os
import secrets

root = Path(__file__).resolve().parents[2] / 'firmware/heltec'
target = root / 'include/secrets.h'
credentials = root / '.local/credentials.json'
if target.exists() or credentials.exists():
    raise SystemExit('Local configuration already exists; refusing to replace credentials.')
(root / '.local').mkdir(parents=True, exist_ok=True)
data = {'ap_password': secrets.token_hex(12), 'ota_password': secrets.token_hex(16)}
for path, content in [
    (credentials, json.dumps(data, indent=2)+'\n'),
    (target, '#pragma once\n#define AP_PASSWORD '+json.dumps(data['ap_password'])+'\n#define OTA_PASSWORD '+json.dumps(data['ota_password'])+'\n')
]:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(content)
print('Generated local-only credentials and secrets.h; do not commit either file.')
