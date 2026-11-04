"""Modulo di cifratura autenticata per lo sblocco rapido delle chiavi API tramite password.

Utilizza PBKDF2-HMAC-SHA256 (200.000 iterazioni) + cifratura stream HMAC-SHA256 in modalità counter
con autenticazione MAC EtM (Encrypt-then-MAC). Né la password né le chiavi in chiaro compaiono mai
nel codice sorgente, rendendo il repository sicuro per la pubblicazione su GitHub.
"""

import base64
import hashlib
import hmac
import json
from typing import Dict, Optional

_PBKDF2_ITERATIONS = 200_000
_SALT_B64 = "j0oskeezBdZBmMLwe246WQ=="
_NONCE_B64 = "PHG54CSoXx1rkE4jp8WB0g=="
_CIPHERTEXT_B64 = (
    "h5SHYct2vCeSFd62dpUqEe0wMv4R1Bdb9HlOQhqIYbtLbEkRKzKogk+UJMDyIEEdMOfEZBTBN4dHIWNN"
    "5jccLYTbW2Dvk/87AWVOFUFXnJB46oRquUYexdzkpXp2krDFeGC5yN1+tM+TJufU6QwzV00SaTfT22m3"
    "l6HQu1cxpZSFCH8iCYxMHDtR4oGLS60/mfTNGvy60sjGWvHRT9vgL+CsvjOUYdq3H+2LhJVE0UkOKjq4"
    "6FTTi1kFvwL2e+OfKhMHMgyGz0km1EdjdawD43uegkXnUY9o1HVx+fpfu2WfUp0ETwPiSeKsXixlY2iV"
    "iyDShzlA"
)
_TAG_B64 = "Kd0XJZ9v+TGgU6YWbMCejqEvdUZ5vnSpNLTxD/HJk+c="


def encrypt_keys_bundle(
    password: str, keys: Dict[str, str], salt: bytes, nonce: bytes
) -> tuple[str, str, str, str]:
    """Cifra un dizionario di chiavi con PBKDF2-HMAC-SHA256 + stream HMAC-SHA256 + EtM MAC."""
    pwd = password.strip()
    dk = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"), salt, _PBKDF2_ITERATIONS, dklen=64)
    enc_key, mac_key = dk[:32], dk[32:]

    plaintext = json.dumps(keys, ensure_ascii=False).encode("utf-8")
    stream = bytearray()
    counter = 0
    while len(stream) < len(plaintext):
        stream.extend(hmac.new(enc_key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest())
        counter += 1

    ciphertext = bytes(a ^ b for a, b in zip(plaintext, stream[: len(plaintext)]))
    tag = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    return (
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(nonce).decode("ascii"),
        base64.b64encode(ciphertext).decode("ascii"),
        base64.b64encode(tag).decode("ascii"),
    )


def decrypt_keys_bundle(
    password: str, salt_b64: str, nonce_b64: str, ciphertext_b64: str, tag_b64: str
) -> Optional[Dict[str, str]]:
    """Decifra e autentica un bundle cifrato con encrypt_keys_bundle."""
    if not password or not isinstance(password, str):
        return None
    pwd = password.strip()
    if not pwd:
        return None

    try:
        salt = base64.b64decode(salt_b64)
        nonce = base64.b64decode(nonce_b64)
        ciphertext = base64.b64decode(ciphertext_b64)
        expected_tag = base64.b64decode(tag_b64)

        dk = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"), salt, _PBKDF2_ITERATIONS, dklen=64)
        enc_key, mac_key = dk[:32], dk[32:]

        computed_tag = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(computed_tag, expected_tag):
            return None

        stream = bytearray()
        counter = 0
        while len(stream) < len(ciphertext):
            stream.extend(hmac.new(enc_key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest())
            counter += 1

        plaintext = bytes(a ^ b for a, b in zip(ciphertext, stream[: len(ciphertext)]))
        data = json.loads(plaintext.decode("utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
        return None
    except Exception:
        return None


def unlock_giovanni_keys(password: str) -> Optional[Dict[str, str]]:
    """Decifra e restituisce il dizionario con le 3 chiavi API se la password è corretta, altrimenti None."""
    return decrypt_keys_bundle(password, _SALT_B64, _NONCE_B64, _CIPHERTEXT_B64, _TAG_B64)

