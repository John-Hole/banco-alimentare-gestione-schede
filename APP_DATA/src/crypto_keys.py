"""Modulo di cifratura autenticata per lo sblocco rapido delle chiavi API tramite password.

Utilizza PBKDF2-HMAC-SHA256 (200.000 iterazioni) + cifratura stream HMAC-SHA256 in modalità counter
con autenticazione MAC EtM (Encrypt-then-MAC). Né la password né le chiavi in chiaro compaiono mai
nel codice sorgente, rendendo il repository sicuro per la pubblicazione su GitHub.
"""

import ast
import base64
import getpass
import hashlib
import hmac
import json
import os
import re
import sys
import tempfile
from pathlib import Path
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

_SEALABLE_CONFIG_KEYS = (
    "gemini_api_key",
    "gemini_api_key_2",
    "openrouter_api_key",
    "trello_api_key",
    "trello_token",
    "trello_list_id",
)


def _safe_print(msg: str) -> None:
    """Stampa messaggi CLI evitando UnicodeEncodeError su console Windows cp1252."""
    enc = getattr(sys.stdout, "encoding", None)
    if enc:
        try:
            msg.encode(enc)
            print(msg)
            return
        except (UnicodeEncodeError, LookupError):
            pass
    else:
        try:
            print(msg)
            return
        except UnicodeEncodeError:
            pass

    fallback = (
        msg.replace("🔑", "[CHIAVI]")
        .replace("❌", "[ERRORE]")
        .replace("⚠️", "[ATTENZIONE]")
        .replace("⚠", "[ATTENZIONE]")
        .replace("✅", "[OK]")
        .replace("✓", "[OK]")
        .replace("•", "-")
    )
    target_enc = enc or "ascii"
    try:
        safe_text = fallback.encode(target_enc, errors="replace").decode(target_enc, errors="replace")
    except LookupError:
        safe_text = fallback.encode("ascii", errors="replace").decode("ascii", errors="replace")
    print(safe_text)


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
    """Decifra il bundle cifrato e restituisce le chiavi se la password è corretta, altrimenti None.

    Il bundle contiene le chiavi IA (``gemini_api_key``, ``gemini_api_key_2``,
    ``openrouter_api_key``) e, quando sigillate nel bundle, anche le credenziali Trello
    (``trello_api_key``, ``trello_token``, ``trello_list_id``).
    """
    return decrypt_keys_bundle(password, _SALT_B64, _NONCE_B64, _CIPHERTEXT_B64, _TAG_B64)


def _format_ciphertext_assignment(ciphertext_b64: str) -> str:
    chunks = [ciphertext_b64[i : i + 80] for i in range(0, len(ciphertext_b64), 80)] or [""]
    if len(chunks) == 1:
        return f'_CIPHERTEXT_B64 = "{chunks[0]}"'
    body = "\n".join(f'    "{chunk}"' for chunk in chunks)
    return f"_CIPHERTEXT_B64 = (\n{body}\n)"


def _extract_bundle_constants_from_source(source_text: str) -> tuple[str, str, str, str]:
    tree = ast.parse(source_text)
    found: Dict[str, str] = {}
    targets = {"_SALT_B64", "_NONCE_B64", "_CIPHERTEXT_B64", "_TAG_B64"}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in targets:
                val = ast.literal_eval(node.value)
                if not isinstance(val, str):
                    raise ValueError(f"La costante {name} non è una stringa valida.")
                found[name] = val
    missing = targets - set(found.keys())
    if missing:
        raise ValueError(f"Costanti bundle mancanti nel file sorgente: {', '.join(sorted(missing))}")
    return (
        found["_SALT_B64"],
        found["_NONCE_B64"],
        found["_CIPHERTEXT_B64"],
        found["_TAG_B64"],
    )


def update_encrypted_bundle_file(
    password: str,
    keys: Dict[str, str],
    target_path: Optional[Path] = None,
) -> None:
    """Rigenera salt e nonce casuali, cifra ``keys`` e aggiorna le 4 costanti nel file target."""
    if not isinstance(password, str) or not password.strip():
        raise ValueError("La password non può essere vuota.")
    if not isinstance(keys, dict) or not keys:
        raise ValueError("Il dizionario delle chiavi non può essere vuoto.")
    if any(v is None for v in keys.values()):
        raise ValueError("I valori delle chiavi non possono essere None.")

    clean_keys = {str(k): str(v) for k, v in keys.items()}
    target = Path(target_path) if target_path is not None else Path(__file__)
    if not target.is_file():
        raise FileNotFoundError(f"File target non trovato: {target}")

    salt = os.urandom(16)
    nonce = os.urandom(16)
    salt_b64, nonce_b64, ct_b64, tag_b64 = encrypt_keys_bundle(password, clean_keys, salt, nonce)

    if decrypt_keys_bundle(password, salt_b64, nonce_b64, ct_b64, tag_b64) != clean_keys:
        raise RuntimeError("Verifica round-trip in memoria fallita prima della scrittura.")

    content = target.read_text(encoding="utf-8")
    replacements = [
        (r"""^_SALT_B64\s*=\s*(?:"[^"]*"|'[^']*')""", f'_SALT_B64 = "{salt_b64}"', "_SALT_B64"),
        (r"""^_NONCE_B64\s*=\s*(?:"[^"]*"|'[^']*')""", f'_NONCE_B64 = "{nonce_b64}"', "_NONCE_B64"),
        (
            r"""^_CIPHERTEXT_B64\s*=\s*(?:"[^"]*"|'[^']*'|\(\s*(?:(?:"[^"]*"|'[^']*')\s*)+\))""",
            _format_ciphertext_assignment(ct_b64),
            "_CIPHERTEXT_B64",
        ),
        (r"""^_TAG_B64\s*=\s*(?:"[^"]*"|'[^']*')""", f'_TAG_B64 = "{tag_b64}"', "_TAG_B64"),
    ]

    updated = content
    for pattern, repl, const_name in replacements:
        updated, count = re.subn(pattern, lambda _m, r=repl: r, updated, count=1, flags=re.MULTILINE)
        if count != 1:
            raise ValueError(f"Impossibile aggiornare la costante {const_name} in {target}")

    parsed_constants = _extract_bundle_constants_from_source(updated)
    if decrypt_keys_bundle(password, *parsed_constants) != clean_keys:
        raise RuntimeError("Verifica round-trip sulle costanti aggiornate fallita.")

    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.stem}_",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_file.write(updated)
            tmp_path = Path(tmp_file.name)
        os.replace(tmp_path, target)
        tmp_path = None
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

    persisted_constants = _extract_bundle_constants_from_source(target.read_text(encoding="utf-8"))
    if decrypt_keys_bundle(password, *persisted_constants) != clean_keys:
        raise RuntimeError("Verifica round-trip dopo scrittura su disco fallita.")

    try:
        if target.resolve() == Path(__file__).resolve():
            globals()["_SALT_B64"] = salt_b64
            globals()["_NONCE_B64"] = nonce_b64
            globals()["_CIPHERTEXT_B64"] = ct_b64
            globals()["_TAG_B64"] = tag_b64
    except OSError:
        pass


def seal_from_config_interactive(
    config_path: Optional[Path] = None,
    target_path: Optional[Path] = None,
) -> bool:
    """Entrypoint CLI interattivo per sigillare nel bundle le chiavi non vuote di APP_DATA/config.json."""
    cfg_file = Path(config_path) if config_path is not None else Path(__file__).resolve().parent.parent / "config.json"
    if not cfg_file.is_file():
        _safe_print(f"❌ File di configurazione non trovato: {cfg_file}")
        return False

    try:
        raw_cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    except Exception as exc:
        _safe_print(f"❌ Errore nella lettura di {cfg_file}: {exc}")
        return False

    if not isinstance(raw_cfg, dict):
        _safe_print(f"❌ Formato config.json non valido in {cfg_file}.")
        return False

    keys_to_seal: Dict[str, str] = {}
    for key_name in _SEALABLE_CONFIG_KEYS:
        val = str(raw_cfg.get(key_name) or "").strip()
        if val:
            keys_to_seal[key_name] = val

    if not keys_to_seal:
        _safe_print("❌ Nessuna chiave non vuota trovata in config.json tra: " + ", ".join(_SEALABLE_CONFIG_KEYS))
        return False

    is_self_target = target_path is None or Path(target_path).resolve() == Path(__file__).resolve()
    if not is_self_target and not Path(target_path).is_file():
        _safe_print(f"❌ File target non trovato: {target_path}")
        return False

    _safe_print("🔑 Chiavi raccolte da config.json (valori omessi per sicurezza):")
    for key_name in keys_to_seal:
        _safe_print(f"   • {key_name}")

    try:
        pwd = getpass.getpass("Inserisci la password del profilo per sigillare il bundle: ").strip()
    except (EOFError, KeyboardInterrupt):
        _safe_print("\n❌ Operazione annullata.")
        return False

    if not pwd:
        _safe_print("❌ Password vuota: operazione annullata.")
        return False

    try:
        if is_self_target:
            existing = unlock_giovanni_keys(pwd)
        else:
            constants = _extract_bundle_constants_from_source(Path(target_path).read_text(encoding="utf-8"))
            existing = decrypt_keys_bundle(pwd, *constants)
    except Exception as exc:
        _safe_print(f"❌ Impossibile leggere il bundle esistente dal file target: {exc}")
        return False

    if existing is not None:
        _safe_print("✓ Password verificata con il bundle attuale. Rigenerazione salt/nonce e aggiornamento...")
    else:
        _safe_print("⚠️ La password inserita non coincide con il bundle precedente.")
        try:
            confirm = input("Digita esattamente 'SI' per confermare e impostarla come nuova password: ").strip()
        except (EOFError, KeyboardInterrupt):
            _safe_print("\n❌ Operazione annullata.")
            return False
        if confirm != "SI":
            _safe_print("Operazione annullata.")
            return False

    try:
        update_encrypted_bundle_file(pwd, keys_to_seal, target_path=target_path)

        if is_self_target:
            verified = unlock_giovanni_keys(pwd)
        else:
            constants_after = _extract_bundle_constants_from_source(Path(target_path).read_text(encoding="utf-8"))
            verified = decrypt_keys_bundle(pwd, *constants_after)
    except Exception as exc:
        _safe_print(f"❌ Errore durante l'aggiornamento del bundle: {exc}")
        return False

    if verified != keys_to_seal:
        _safe_print("❌ Errore: unlock_giovanni_keys non ha restituito le chiavi attese dopo l'aggiornamento.")
        return False

    _safe_print(f"✅ Bundle aggiornato e verificato ({len(verified)} chiavi sigillate: {', '.join(verified.keys())}).")
    return True


if __name__ == "__main__":
    raise SystemExit(0 if seal_from_config_interactive() else 1)

