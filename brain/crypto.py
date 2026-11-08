"""Password encryption for everything ClipRush publishes (the repo is public to stay free).
PBKDF2-SHA256 (200k rounds) -> AES-256-GCM over gzip. The web app decrypts the same
format with the browser's built-in WebCrypto, so no third-party code touches your data."""
from __future__ import annotations

import base64
import gzip
import json
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITER = 200_000


def _key(password: str, salt: bytes, iters: int = ITER) -> bytes:
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iters).derive(password.encode())


def encrypt_obj(obj, password: str) -> bytes:
    raw = gzip.compress(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str).encode(), 6)
    salt, iv = os.urandom(16), os.urandom(12)
    ct = AESGCM(_key(password, salt)).encrypt(iv, raw, None)
    env = {"v": 1, "kdf": "pbkdf2-sha256", "iter": ITER, "salt": base64.b64encode(salt).decode(),
           "iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode()}
    return json.dumps(env).encode()


def decrypt_obj(blob: bytes, password: str):
    env = json.loads(blob)
    salt, iv, ct = (base64.b64decode(env[k]) for k in ("salt", "iv", "ct"))
    raw = AESGCM(_key(password, salt, int(env.get("iter", ITER)))).decrypt(iv, ct, None)
    return json.loads(gzip.decompress(raw))
