"""Servizio locale e cloud opzionale per suggerimenti, bug e task operativi (senza login).

Caratteristiche:
- Scrittura immediata offline su file task Markdown: SUGGERIMENTI/task_suggerimenti_<ANNO>.md
- Archivio strutturato JSONL: SUGGERIMENTI/suggerimenti.jsonl
- Invio opzionale automatico verso una lista Trello (se trello_api_key, trello_token, trello_list_id configurati)
- Coda di sincronizzazione differita (retry) per eventuali suggerimenti creati offline
- Link rapido precompilato per GitHub Issues come canale opzionale aggiuntivo
"""

import json
import os
import re
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import requests

from ..config import SUGGERIMENTI_DIR, load_app_config
from .session_logger import get_session_logger


FEEDBACK_CATEGORIES: List[str] = [
    "Suggerimento Funzionalità",
    "Segnalazione Errore / Bug",
    "Qualità Lettura IA",
    "Interfaccia & Usabilità",
    "Scanner & Excel",
]

GITHUB_NEW_ISSUE_URL = "https://github.com/John-Hole/banco-alimentare-gestione-schede/issues/new"
TRELLO_CARDS_ENDPOINT = "https://api.trello.com/1/cards"


def sanitize_context_for_feedback(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Filtra il dizionario di contesto assicurando che non contenga mai chiavi API o token."""
    if not context or not isinstance(context, dict):
        return {}
    safe: Dict[str, Any] = {}
    blocked_substrings = ("key", "token", "secret", "password", "auth")
    for k, v in context.items():
        lk = str(k).lower()
        if any(b in lk for b in blocked_substrings):
            continue
        if v is not None and str(v).strip():
            safe[str(k)] = str(v).strip()
    return safe


class FeedbackService:
    """Gestisce il salvataggio locale in stile Task Checklist e la sincronizzazione opzionale Trello."""

    _instance: Optional["FeedbackService"] = None
    _lock = threading.Lock()

    def __init__(self, custom_dir: Optional[Path] = None):
        self.feedback_dir = Path(custom_dir) if custom_dir else SUGGERIMENTI_DIR
        self.feedback_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.feedback_dir / "suggerimenti.jsonl"

    @classmethod
    def get_instance(cls) -> "FeedbackService":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def get_markdown_path(self, year: Optional[int] = None) -> Path:
        yr = int(year or datetime.now().year)
        return self.feedback_dir / f"task_suggerimenti_{yr}.md"

    @staticmethod
    def has_trello_configured(config: Optional[Dict[str, Any]] = None) -> bool:
        cfg = config if config is not None else load_app_config()
        return bool(
            str(cfg.get("trello_api_key") or "").strip()
            and str(cfg.get("trello_token") or "").strip()
            and str(cfg.get("trello_list_id") or "").strip()
        )

    @staticmethod
    def _post_trello_card(
        api_key: str,
        token: str,
        list_id: str,
        name: str,
        desc: str,
        timeout: float = 8.0,
    ) -> Dict[str, Any]:
        """Crea una Trello Card sulla lista configurata tramite API REST v1."""
        try:
            resp = requests.post(
                TRELLO_CARDS_ENDPOINT,
                params={"key": api_key.strip(), "token": token.strip()},
                data={
                    "idList": list_id.strip(),
                    "name": name[:160],
                    "desc": desc,
                },
                timeout=timeout,
            )
            if resp.status_code in (200, 201):
                payload = resp.json() if hasattr(resp, "json") else {}
                return {
                    "ok": True,
                    "card_id": str(payload.get("id", "")),
                    "card_url": str(payload.get("shortUrl") or payload.get("url") or ""),
                    "error": "",
                }
            detail = ""
            try:
                detail = resp.text[:200]
            except Exception:
                detail = ""
            return {
                "ok": False,
                "card_id": "",
                "card_url": "",
                "error": f"HTTP {resp.status_code}: {detail}".strip(),
            }
        except Exception as e:
            return {
                "ok": False,
                "card_id": "",
                "card_url": "",
                "error": str(e),
            }

    @staticmethod
    def _build_trello_card_content(record: Dict[str, Any]) -> tuple[str, str]:
        cat = record.get("category") or "Suggerimento Funzionalità"
        msg = str(record.get("message") or "").strip()
        first_line = msg.splitlines()[0].strip() if msg else "Suggerimento dall'app"
        short_title = first_line[:95] + ("..." if len(first_line) > 95 else "")
        card_name = f"[{cat}] {short_title}"

        ctx = record.get("context") or {}
        ctx_lines = [f"- **{k}**: {v}" for k, v in ctx.items()]
        ctx_block = "\n".join(ctx_lines) if ctx_lines else "- Nessun dettaglio tecnico aggiuntivo"

        card_desc = (
            f"**Autore / Postazione:** {record.get('author') or 'Anonimo'}\n"
            f"**Categoria:** {cat}\n"
            f"**Data e Ora:** {record.get('created_at', '')}\n\n"
            f"### Messaggio\n{msg}\n\n"
            f"### Contesto Sessione\n{ctx_block}\n"
        )
        return card_name, card_desc

    def _ensure_markdown_header(self, md_path: Path, year: int):
        if md_path.exists() and md_path.stat().st_size > 0:
            return
        header = (
            f"# 💡 Consigli, Task e Miglioramenti — Banco Alimentare ({year})\n\n"
            "Elenco locale salvato automaticamente dal pulsante **💡 Consigli**.\n"
            "Puoi aprire questo file o spuntare le attività completate (`- [x]`).\n\n"
        )
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(header)

    def _append_markdown_task(self, record: Dict[str, Any]) -> Path:
        now_dt = datetime.fromisoformat(record["created_at"])
        md_path = self.get_markdown_path(now_dt.year)
        self._ensure_markdown_header(md_path, now_dt.year)

        author = record.get("author") or "Anonimo"
        cat = record.get("category") or "Suggerimento"
        ts_human = now_dt.strftime("%Y-%m-%d %H:%M")
        ctx = record.get("context") or {}

        ctx_parts = []
        if ctx.get("provider"):
            ctx_parts.append(f"IA: {ctx['provider']}")
        if ctx.get("supermercato"):
            ctx_parts.append(f"Supermercato: {ctx['supermercato']}")
        ctx_str = f" · {' | '.join(ctx_parts)}" if ctx_parts else ""

        raw_lines = [ln.strip() for ln in str(record.get("message") or "").splitlines() if ln.strip()]
        headline = re.sub(r"\s+", " ", raw_lines[0]) if raw_lines else ""
        extra_lines = raw_lines[1:]

        trello_tag = " · 📌 Trello OK" if record.get("trello_synced") else ""
        entry_text = f"- [ ] **[{cat}]** {headline} *(Da: {author} · {ts_human}{ctx_str}{trello_tag})*\n"
        for extra in extra_lines:
            entry_text += f"  > {extra}\n"

        with open(md_path, "a", encoding="utf-8") as f:
            f.write(entry_text)
        return md_path

    def submit_feedback(
        self,
        author: str,
        category: str,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Registra un nuovo suggerimento/bug sia localmente (.md + .jsonl) che su Trello (se configurato)."""
        clean_msg = str(message or "").strip()
        if not clean_msg:
            return {"success": False, "error": "Inserisci almeno una breve descrizione prima di inviare."}

        clean_author = str(author or "").strip() or "Anonimo"
        clean_cat = str(category or "").strip() or FEEDBACK_CATEGORIES[0]
        safe_ctx = sanitize_context_for_feedback(context)
        now = datetime.now()
        entry_id = f"fb_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

        record: Dict[str, Any] = {
            "id": entry_id,
            "created_at": now.isoformat(),
            "author": clean_author,
            "category": clean_cat,
            "message": clean_msg,
            "context": safe_ctx,
            "status": "open",
            "trello_synced": False,
            "trello_card_id": "",
            "trello_card_url": "",
            "trello_error": "",
        }

        cfg = config if config is not None else load_app_config()
        trello_attempted = self.has_trello_configured(cfg)
        if trello_attempted:
            card_name, card_desc = self._build_trello_card_content(record)
            res_trello = self._post_trello_card(
                api_key=str(cfg.get("trello_api_key", "")),
                token=str(cfg.get("trello_token", "")),
                list_id=str(cfg.get("trello_list_id", "")),
                name=card_name,
                desc=card_desc,
            )
            record["trello_synced"] = bool(res_trello.get("ok"))
            record["trello_card_id"] = res_trello.get("card_id", "")
            record["trello_card_url"] = res_trello.get("card_url", "")
            record["trello_error"] = res_trello.get("error", "")

        with self._lock:
            self.feedback_dir.mkdir(parents=True, exist_ok=True)
            md_path = self._append_markdown_task(record)
            with open(self.jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        try:
            get_session_logger().logger.info(
                f"[CONSIGLI] Salvato task locale '{entry_id}' [{clean_cat}] | Trello: "
                f"{'OK' if record['trello_synced'] else ('Non inviato: ' + record['trello_error'] if trello_attempted else 'Solo locale')}"
            )
        except Exception:
            pass

        return {
            "success": True,
            "record": record,
            "markdown_path": str(md_path),
            "jsonl_path": str(self.jsonl_path),
            "trello_attempted": trello_attempted,
            "trello_synced": record["trello_synced"],
            "trello_card_url": record["trello_card_url"],
            "trello_error": record["trello_error"],
        }

    def list_local_feedback(self, limit: int = 30) -> List[Dict[str, Any]]:
        """Restituisce gli ultimi suggerimenti locali in ordine cronologico inverso."""
        with self._lock:
            if not self.jsonl_path.exists():
                return []
            items: List[Dict[str, Any]] = []
            try:
                with open(self.jsonl_path, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            items.append(json.loads(line))
                        except Exception:
                            continue
            except Exception:
                return []
        items.reverse()
        if limit and limit > 0:
            return items[:limit]
        return items

    def count_pending_trello(self) -> int:
        """Conta quanti task locali non sono ancora stati sincronizzati con Trello."""
        return sum(1 for item in self.list_local_feedback(limit=0) if not item.get("trello_synced"))

    def _mark_markdown_entries_synced(self, synced_records: List[Dict[str, Any]]) -> None:
        """Aggiorna le righe Markdown corrispondenti aggiungendo il tag '📌 Trello OK' dopo un sync riuscito."""
        by_year: Dict[int, List[Dict[str, Any]]] = {}
        for rec in synced_records:
            try:
                dt = datetime.fromisoformat(str(rec.get("created_at") or ""))
                yr = dt.year
            except Exception:
                yr = datetime.now().year
            by_year.setdefault(yr, []).append(rec)

        for yr, recs in by_year.items():
            md_path = self.get_markdown_path(yr)
            if not md_path.exists():
                continue
            try:
                lines = md_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
                changed = False
                for rec in recs:
                    try:
                        ts_human = datetime.fromisoformat(str(rec.get("created_at") or "")).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        continue
                    cat = rec.get("category") or "Suggerimento"
                    author = rec.get("author") or "Anonimo"
                    raw_lines = [ln.strip() for ln in str(rec.get("message") or "").splitlines() if ln.strip()]
                    headline = re.sub(r"\s+", " ", raw_lines[0]) if raw_lines else ""
                    prefix = f"- [ ] **[{cat}]** {headline} *(Da: {author} · {ts_human}"
                    for i, line in enumerate(lines):
                        stripped = line.rstrip("\r\n")
                        if line.startswith(prefix) and "📌 Trello OK" not in line and stripped.endswith(")*"):
                            ending = line[len(stripped):]
                            lines[i] = f"{stripped[:-2]} · 📌 Trello OK)*{ending}"
                            changed = True
                            break
                if changed:
                    md_path.write_text("".join(lines), encoding="utf-8")
            except Exception:
                pass

    def sync_pending_to_trello(self, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Ritenta l'invio su Trello di tutti i suggerimenti locali non ancora sincronizzati (senza bloccare _lock in rete)."""
        cfg = config if config is not None else load_app_config()
        if not self.has_trello_configured(cfg):
            return {
                "configured": False,
                "attempted": 0,
                "synced": 0,
                "failed": 0,
                "errors": ["Configura Chiave API, Token e ID Lista Trello nelle Impostazioni."],
            }

        with self._lock:
            if not self.jsonl_path.exists():
                return {"configured": True, "attempted": 0, "synced": 0, "failed": 0, "errors": []}

            pending_jobs: List[tuple[str, str, str]] = []
            with open(self.jsonl_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    s = line.strip()
                    if not s:
                        continue
                    try:
                        rec = json.loads(s)
                    except Exception:
                        continue
                    rec_id = str(rec.get("id") or "").strip()
                    if rec_id and not rec.get("trello_synced"):
                        card_name, card_desc = self._build_trello_card_content(rec)
                        pending_jobs.append((rec_id, card_name, card_desc))

        if not pending_jobs:
            return {"configured": True, "attempted": 0, "synced": 0, "failed": 0, "errors": []}

        # Esegue le chiamate HTTP fuori dal lock per non bloccare invii o letture UI concorrenti
        api_key = str(cfg.get("trello_api_key", ""))
        token = str(cfg.get("trello_token", ""))
        list_id = str(cfg.get("trello_list_id", ""))
        outcomes: Dict[str, Dict[str, Any]] = {}
        attempted = 0
        synced = 0
        failed = 0
        errors: List[str] = []

        for rec_id, card_name, card_desc in pending_jobs:
            attempted += 1
            res = self._post_trello_card(
                api_key=api_key,
                token=token,
                list_id=list_id,
                name=card_name,
                desc=card_desc,
            )
            outcomes[rec_id] = res
            if res.get("ok"):
                synced += 1
            else:
                failed += 1
                err_txt = str(res.get("error", "") or "")
                if err_txt:
                    errors.append(err_txt)

        # Rilegge lo stato corrente sotto lock per preservare eventuali nuovi invii concorrenti
        synced_records: List[Dict[str, Any]] = []
        with self._lock:
            all_records: List[Dict[str, Any]] = []
            if self.jsonl_path.exists():
                with open(self.jsonl_path, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        s = line.strip()
                        if not s:
                            continue
                        try:
                            all_records.append(json.loads(s))
                        except Exception:
                            continue

            for rec in all_records:
                rec_id = str(rec.get("id") or "").strip()
                if rec_id not in outcomes:
                    continue
                res = outcomes[rec_id]
                if res.get("ok"):
                    rec["trello_synced"] = True
                    rec["trello_card_id"] = res.get("card_id", "")
                    rec["trello_card_url"] = res.get("card_url", "")
                    rec["trello_error"] = ""
                    synced_records.append(rec)
                else:
                    rec["trello_error"] = res.get("error", "")

            if attempted > 0:
                with open(self.jsonl_path, "w", encoding="utf-8") as f:
                    for rec in all_records:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

            if synced_records:
                self._mark_markdown_entries_synced(synced_records)

        return {
            "configured": True,
            "attempted": attempted,
            "synced": synced,
            "failed": failed,
            "errors": errors,
        }

    @staticmethod
    def build_github_issue_url(title: str, body: str, category: str = "") -> str:
        """Costruisce l'URL GitHub Issues precompilato per apertura rapida nel browser."""
        cat_lower = str(category or "").lower()
        labels = "bug" if ("bug" in cat_lower or "errore" in cat_lower) else "enhancement"
        params = urlencode({
            "title": title[:120],
            "body": body,
            "labels": labels,
        })
        return f"{GITHUB_NEW_ISSUE_URL}?{params}"

    def open_markdown(self) -> bool:
        """Apre il file task_suggerimenti_<ANNO>.md nell'editor predefinito."""
        md_path = self.get_markdown_path()
        self.feedback_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_markdown_header(md_path, datetime.now().year)
        try:
            if os.name == "nt":
                os.startfile(str(md_path))
                return True
            subprocess.Popen(["xdg-open", str(md_path)])
            return True
        except Exception:
            try:
                subprocess.Popen(["notepad.exe", str(md_path)])
                return True
            except Exception:
                return False

    def open_folder(self) -> bool:
        """Apre la cartella SUGGERIMENTI in Esplora Risorse."""
        self.feedback_dir.mkdir(parents=True, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(str(self.feedback_dir))
                return True
            subprocess.Popen(["xdg-open", str(self.feedback_dir)])
            return True
        except Exception:
            return False


def get_feedback_service() -> FeedbackService:
    """Restituisce l'istanza singleton di FeedbackService."""
    return FeedbackService.get_instance()
