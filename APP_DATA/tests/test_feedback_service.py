"""Unit tests per FeedbackService e FeedbackDialogV3 (Suggerimenti locali, coda Trello e GitHub Issue URL)."""

import json
import sys
import tempfile
import time
import tkinter as tk
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.feedback_service import (
    FEEDBACK_CATEGORIES,
    GITHUB_NEW_ISSUE_URL,
    TRELLO_CARDS_ENDPOINT,
    FeedbackService,
    sanitize_context_for_feedback,
)
from src.ui.components_dialogs import (
    FeedbackDialog,
    FeedbackDialogV3,
    FeedbackPanel,
    FeedbackPanelV3,
    SettingsDialogV3,
)


class TestFeedbackSanitizationAndConfig(unittest.TestCase):
    """Verifica la sanitizzazione del contesto e il rilevamento della configurazione Trello."""

    def test_sanitize_context_strips_secrets_and_blank_values(self):
        self.assertEqual(sanitize_context_for_feedback(None), {})
        self.assertEqual(sanitize_context_for_feedback({}), {})
        self.assertEqual(sanitize_context_for_feedback("not-a-dict"), {})  # type: ignore[arg-type]

        raw = {
            "provider": "  cascade_custom  ",
            "supermercato": "COOP",
            "gemini_api_key": "AIzaSecret123",
            "openrouter_api_key": "sk-or-secret",
            "trello_api_key": "tr_key",
            "trello_token": "tr_tok",
            "Client_Secret": "shh",
            "user_password": "pw",
            "auth_header": "Bearer xyz",
            "empty_field": "   ",
            "none_field": None,
            "foglio_attivo": "Foglio 1",
        }
        cleaned = sanitize_context_for_feedback(raw)
        self.assertEqual(
            cleaned,
            {
                "provider": "cascade_custom",
                "supermercato": "COOP",
                "foglio_attivo": "Foglio 1",
            },
        )

    def test_has_trello_configured_requires_all_three_fields(self):
        self.assertFalse(FeedbackService.has_trello_configured({}))
        self.assertFalse(
            FeedbackService.has_trello_configured(
                {"trello_api_key": "k", "trello_token": "t", "trello_list_id": "   "}
            )
        )
        self.assertFalse(
            FeedbackService.has_trello_configured(
                {"trello_api_key": "", "trello_token": "t", "trello_list_id": "l"}
            )
        )
        self.assertTrue(
            FeedbackService.has_trello_configured(
                {"trello_api_key": "k", "trello_token": "t", "trello_list_id": "l"}
            )
        )


class TestFeedbackServiceLocalAndTrello(unittest.TestCase):
    """Verifica persistenza offline (.md + .jsonl), sync Trello e retry pendenti in directory isolata."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = FeedbackService(custom_dir=Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_submit_rejects_empty_or_whitespace_message(self):
        for bad in ("", "   \n\t  ", None):
            res = self.svc.submit_feedback("Marco", FEEDBACK_CATEGORIES[0], bad, config={})  # type: ignore[arg-type]
            self.assertFalse(res["success"])
            self.assertIn("error", res)

        self.assertFalse(self.svc.jsonl_path.exists())
        self.assertFalse(self.svc.get_markdown_path().exists())
        self.assertEqual(self.svc.list_local_feedback(), [])
        self.assertEqual(self.svc.count_pending_trello(), 0)

    def test_local_submit_writes_markdown_checklist_and_jsonl_in_reverse_order(self):
        res1 = self.svc.submit_feedback(
            author="",
            category="",
            message="Prima riga consiglio\nSeconda riga dettaglio",
            context={"provider": "gemini_single", "supermercato": "Esselunga", "gemini_api_key": "SECRET"},
            config={},
        )
        self.assertTrue(res1["success"])
        self.assertFalse(res1["trello_attempted"])
        self.assertFalse(res1["trello_synced"])

        res2 = self.svc.submit_feedback(
            author="Lucia",
            category="Scanner & Excel",
            message="Secondo suggerimento DPI",
            context={},
            config={},
        )
        self.assertTrue(res2["success"])

        md_path = self.svc.get_markdown_path(datetime.now().year)
        self.assertTrue(md_path.exists())
        md_text = md_path.read_text(encoding="utf-8")
        self.assertEqual(md_text.count("# 💡 Consigli, Task e Miglioramenti"), 1)
        self.assertIn(f"- [ ] **[{FEEDBACK_CATEGORIES[0]}]** Prima riga consiglio", md_text)
        self.assertIn("  > Seconda riga dettaglio", md_text)
        self.assertIn("Da: Anonimo", md_text)
        self.assertIn("IA: gemini_single | Supermercato: Esselunga", md_text)
        self.assertNotIn("SECRET", md_text)
        self.assertIn("- [ ] **[Scanner & Excel]** Secondo suggerimento DPI *(Da: Lucia", md_text)

        items = self.svc.list_local_feedback(limit=10)
        self.assertEqual(len(items), 2)
        # Ordine cronologico inverso
        self.assertEqual(items[0]["message"], "Secondo suggerimento DPI")
        self.assertEqual(items[1]["author"], "Anonimo")
        self.assertEqual(items[1]["context"], {"provider": "gemini_single", "supermercato": "Esselunga"})
        self.assertEqual(self.svc.count_pending_trello(), 2)

        # Limite e resilienza su riga JSONL corrotta
        with open(self.svc.jsonl_path, "a", encoding="utf-8") as f:
            f.write("{corrupt-json-line\n\n")
        limited = self.svc.list_local_feedback(limit=1)
        self.assertEqual(len(limited), 1)
        self.assertEqual(limited[0]["message"], "Secondo suggerimento DPI")

    @patch("src.services.feedback_service.requests.post")
    def test_submit_with_trello_configured_creates_card_on_201(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"id": "card_001", "shortUrl": "https://trello.com/c/card001"}
        mock_post.return_value = mock_resp

        cfg = {
            "trello_api_key": "  tkey_123  ",
            "trello_token": "  ttok_456  ",
            "trello_list_id": "  tlist_789  ",
        }
        long_msg = "A" * 180
        res = self.svc.submit_feedback(
            author="Giulia",
            category="Segnalazione Errore / Bug",
            message=long_msg,
            context={"provider": "cascade_custom", "trello_token": "MUST_NOT_SEND"},
            config=cfg,
        )

        self.assertTrue(res["success"])
        self.assertTrue(res["trello_attempted"])
        self.assertTrue(res["trello_synced"])
        self.assertEqual(res["trello_card_url"], "https://trello.com/c/card001")
        self.assertEqual(res["record"]["trello_card_id"], "card_001")
        self.assertEqual(self.svc.count_pending_trello(), 0)

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], TRELLO_CARDS_ENDPOINT)
        self.assertEqual(kwargs["params"], {"key": "tkey_123", "token": "ttok_456"})
        self.assertEqual(kwargs["data"]["idList"], "tlist_789")
        self.assertLessEqual(len(kwargs["data"]["name"]), 160)
        self.assertNotIn("MUST_NOT_SEND", kwargs["data"]["desc"])

        md_text = Path(res["markdown_path"]).read_text(encoding="utf-8")
        self.assertIn("📌 Trello OK", md_text)

    @patch("src.services.feedback_service.requests.post")
    def test_offline_and_http_error_save_locally_and_sync_pending_recovers(self, mock_post):
        cfg = {
            "trello_api_key": "tkey",
            "trello_token": "ttok",
            "trello_list_id": "tlist",
        }

        # 1. Errore di rete (offline) -> salvato comunque localmente come pendente
        mock_post.side_effect = requests.ConnectionError("Network unreachable")
        r_offline = self.svc.submit_feedback(
            author="Paolo",
            category="Qualità Lettura IA",
            message="Legge 0 invece di O sul foglio",
            config=cfg,
        )
        self.assertTrue(r_offline["success"])
        self.assertTrue(r_offline["trello_attempted"])
        self.assertFalse(r_offline["trello_synced"])
        self.assertIn("Network unreachable", r_offline["trello_error"])

        # 2. HTTP 401 -> salvato localmente come pendente
        resp_401 = MagicMock(status_code=401, text="unauthorized card permission")
        mock_post.side_effect = None
        mock_post.return_value = resp_401
        r_http = self.svc.submit_feedback(
            author="Anna",
            category="Interfaccia & Usabilità",
            message="Pulsante più visibile",
            config=cfg,
        )
        self.assertTrue(r_http["success"])
        self.assertFalse(r_http["trello_synced"])
        self.assertIn("HTTP 401", r_http["trello_error"])
        self.assertEqual(self.svc.count_pending_trello(), 2)

        # 3. Sync senza config -> report non configurato e nessuna chiamata HTTP
        mock_post.reset_mock()
        unconf = self.svc.sync_pending_to_trello(config={})
        self.assertFalse(unconf["configured"])
        self.assertEqual(unconf["attempted"], 0)
        mock_post.assert_not_called()

        # 4. Sync parziale: prima card 201 OK, seconda 500 KO
        resp_201_a = MagicMock(status_code=201)
        resp_201_a.json.return_value = {"id": "c1", "shortUrl": "https://trello.com/c/c1"}
        resp_500 = MagicMock(status_code=500, text="server error")
        mock_post.side_effect = [resp_201_a, resp_500]

        partial = self.svc.sync_pending_to_trello(config=cfg)
        self.assertTrue(partial["configured"])
        self.assertEqual(partial["attempted"], 2)
        self.assertEqual(partial["synced"], 1)
        self.assertEqual(partial["failed"], 1)
        self.assertEqual(self.svc.count_pending_trello(), 1)

        # 5. Secondo passaggio: ritenta SOLO la card rimasta pendente
        resp_201_b = MagicMock(status_code=201)
        resp_201_b.json.return_value = {"id": "c2", "shortUrl": "https://trello.com/c/c2"}
        mock_post.reset_mock()
        mock_post.side_effect = [resp_201_b]

        second = self.svc.sync_pending_to_trello(config=cfg)
        self.assertEqual(second["attempted"], 1)
        self.assertEqual(second["synced"], 1)
        self.assertEqual(second["failed"], 0)
        self.assertEqual(self.svc.count_pending_trello(), 0)
        self.assertEqual(mock_post.call_count, 1)

        # Verifica JSONL aggiornato con entrambi gli ID e nessun duplicato
        lines = [ln for ln in self.svc.jsonl_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        self.assertEqual(len(lines), 2)
        parsed = [json.loads(ln) for ln in lines]
        self.assertTrue(all(rec["trello_synced"] for rec in parsed))
        self.assertEqual({rec["trello_card_id"] for rec in parsed}, {"c1", "c2"})

        # 6. Terzo passaggio quando già tutto sincronizzato -> 0 tentativi HTTP
        mock_post.reset_mock()
        noop = self.svc.sync_pending_to_trello(config=cfg)
        self.assertEqual(noop["attempted"], 0)
        self.assertEqual(noop["synced"], 0)
        mock_post.assert_not_called()

    def test_build_github_issue_url_labels_and_encoding(self):
        bug_url = FeedbackService.build_github_issue_url(
            title="Crash su salvataggio " + ("X" * 150),
            body="Riga 1\nRiga 2 èàù",
            category="Segnalazione Errore / Bug",
        )
        self.assertTrue(bug_url.startswith(GITHUB_NEW_ISSUE_URL + "?"))
        qs_bug = parse_qs(urlparse(bug_url).query)
        self.assertEqual(qs_bug["labels"], ["bug"])
        self.assertLessEqual(len(qs_bug["title"][0]), 120)
        self.assertIn("Riga 2 èàù", qs_bug["body"][0])

        enh_url = FeedbackService.build_github_issue_url(
            title="Nuova idea",
            body="Dettaglio",
            category="Suggerimento Funzionalità",
        )
        qs_enh = parse_qs(urlparse(enh_url).query)
        self.assertEqual(qs_enh["labels"], ["enhancement"])

    def test_sync_pending_nonblocking_concurrent_writes_and_parens_markdown(self):
        import threading

        cfg = {
            "trello_api_key": "tkey",
            "trello_token": "ttok",
            "trello_list_id": "tlist",
        }
        # 1. Registra offline un feedback contenente ')*' nel testo per verificare sostituzione ancorata
        with patch("src.services.feedback_service.requests.post", side_effect=requests.ConnectionError("offline")):
            first = self.svc.submit_feedback(
                author="Luca",
                category="Segnalazione Errore / Bug",
                message="Errore parsing )* carattere speciale",
                config=cfg,
            )
        self.assertFalse(first["trello_synced"])

        in_http = threading.Event()
        release_http = threading.Event()
        concurrent_checks = {"count": None, "write_ok": None}

        def slow_post(*_args, **_kwargs):
            in_http.set()
            release_http.wait(timeout=3.0)
            r = MagicMock(status_code=201)
            r.json.return_value = {"id": "c_sync_1", "shortUrl": "https://trello.com/c/cs1"}
            return r

        sync_res_holder = {}
        with patch("src.services.feedback_service.requests.post", side_effect=slow_post):
            t_sync = threading.Thread(target=lambda: sync_res_holder.update(self.svc.sync_pending_to_trello(config=cfg)))
            t_sync.start()
            self.assertTrue(in_http.wait(timeout=2.0))

            # Mentre HTTP è bloccato in sync_pending_to_trello, sia letture che nuovi invii NON devono bloccarsi
            done_reader = threading.Event()
            def _concurrent_ops():
                concurrent_checks["count"] = self.svc.count_pending_trello()
                w = self.svc.submit_feedback(
                    author="Marta",
                    category="Suggerimento Funzionalità",
                    message="Nuova nota concorrente",
                    config={},
                )
                concurrent_checks["write_ok"] = w.get("success")
                done_reader.set()

            t_conc = threading.Thread(target=_concurrent_ops)
            t_conc.start()
            self.assertTrue(done_reader.wait(timeout=1.0), "Il lock ha bloccato UI/scritture durante l'HTTP Trello")
            t_conc.join(timeout=1.0)
            release_http.set()
            t_sync.join(timeout=2.0)

        self.assertEqual(concurrent_checks["count"], 1)
        self.assertTrue(concurrent_checks["write_ok"])
        self.assertEqual(sync_res_holder.get("synced"), 1)

        # La nota concorrente scritta durante il sync NON deve essere persa dal JSONL
        all_items = self.svc.list_local_feedback(limit=0)
        self.assertEqual(len(all_items), 2)
        msgs = {it["message"]: it for it in all_items}
        self.assertTrue(msgs["Errore parsing )* carattere speciale"]["trello_synced"])
        self.assertFalse(msgs["Nuova nota concorrente"]["trello_synced"])

        # Il file Markdown deve mostrare '📌 Trello OK' in coda alla metadata, MAI dentro 'Errore parsing )* carattere speciale'
        md_lines = self.svc.get_markdown_path().read_text(encoding="utf-8").splitlines()
        first_md_line = [ln for ln in md_lines if "Errore parsing )* carattere speciale" in ln][0]
        self.assertIn("Errore parsing )* carattere speciale *(Da: Luca", first_md_line)
        self.assertTrue(first_md_line.endswith("· 📌 Trello OK)*"))


class TestFeedbackDialogUI(unittest.TestCase):
    """Smoke test headless su FeedbackDialogV3 (submit asincrono, pending sync e link GitHub)."""

    @classmethod
    def setUpClass(cls):
        try:
            import customtkinter as ctk

            cls.root = ctk.CTk()
            cls.root.withdraw()
        except Exception:
            cls.root = None

    @classmethod
    def tearDownClass(cls):
        if cls.root:
            try:
                cls.root.destroy()
            except Exception:
                pass

    def _pump_until(self, predicate, timeout_s: float = 4.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                self.root.update()
            except tk.TclError:
                return False
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def _collect_widget_texts(self, root_widget):
        texts = []
        stack = [root_widget]
        while stack:
            w = stack.pop()
            try:
                txt = w.cget("text")
                if isinstance(txt, str) and txt:
                    texts.append(txt)
            except Exception:
                pass
            try:
                stack.extend(w.winfo_children())
            except Exception:
                pass
        return texts

    def test_feedback_dialog_alias_badges_submit_sync_and_github(self):
        if not self.root:
            self.skipTest("Tkinter non disponibile nell'ambiente di test")

        self.assertIs(FeedbackDialog, FeedbackDialogV3)

        with tempfile.TemporaryDirectory() as tmpdir:
            svc = FeedbackService(custom_dir=Path(tmpdir))
            opened_settings = []

            # 1. Dialog senza Trello: nessun bottone visibile '⚙️ Config. Trello', badge locale e sync mostra avviso admin
            dlg_local = FeedbackDialogV3(
                self.root,
                config={},
                context={"provider": "gemini_single"},
                on_open_settings=lambda: opened_settings.append(True),
                feedback_service=svc,
            )
            try:
                local_texts = self._collect_widget_texts(dlg_local)
                self.assertFalse(
                    any("Config. Trello" in t for t in local_texts),
                    f"Il pulsante Config. Trello non deve essere visibile ai volontari: {local_texts!r}",
                )
                self.assertIn("Solo Documento Locale", dlg_local.trello_badge.cget("text"))
                with patch("src.ui.components_dialogs.messagebox.showwarning") as mock_warn:
                    dlg_local._submit()
                    mock_warn.assert_called_once()
                with patch("src.ui.components_dialogs.messagebox.showinfo") as mock_info:
                    dlg_local._sync_pending()
                    mock_info.assert_called_once()
                    info_title, info_msg = mock_info.call_args[0][0], mock_info.call_args[0][1]
                    self.assertIn("Trello Non Attivo", info_title)
                    self.assertIn("Trello non ancora attivo su questa postazione", info_msg)
                    self.assertIn("Sblocco Rapido Profilo", info_msg)
                with patch("src.ui.components_dialogs.webbrowser.open") as mock_open:
                    dlg_local.message_box.insert("1.0", "Migliora contrasto scansione")
                    dlg_local._open_github()
                    mock_open.assert_called_once()
                    self.assertIn(GITHUB_NEW_ISSUE_URL, mock_open.call_args[0][0])

                dlg_local._open_trello_settings()
                self.assertEqual(opened_settings, [True])
            finally:
                try:
                    if dlg_local.winfo_exists():
                        dlg_local.destroy()
                except Exception:
                    pass

            # 2. Dialog con Trello configurato: submit offline (salva pendente) poi sync pendenti
            cfg = {"trello_api_key": "k", "trello_token": "t", "trello_list_id": "l"}
            dlg = FeedbackDialogV3(
                self.root,
                config=cfg,
                context={"provider": "cascade_custom", "supermercato": "Conad"},
                feedback_service=svc,
            )
            try:
                dlg_texts = self._collect_widget_texts(dlg)
                self.assertFalse(
                    any("Config. Trello" in t for t in dlg_texts),
                    f"Il pulsante Config. Trello non deve essere visibile ai volontari: {dlg_texts!r}",
                )
                self.assertIn("Trello Collegato", dlg.trello_badge.cget("text"))

                with patch(
                    "src.services.feedback_service.requests.post",
                    side_effect=requests.ConnectionError("offline-test"),
                ):
                    dlg.author_var.set("Tester UI")
                    dlg.message_box.insert("1.0", "Task creata offline dalla UI")
                    dlg._submit()
                    done_ok = self._pump_until(
                        lambda: "Trello offline/errore" in dlg.status_lbl.cget("text")
                        and dlg.send_btn.cget("state") == "normal"
                    )
                    self.assertTrue(done_ok, f"Status finale inatteso: {dlg.status_lbl.cget('text')!r}")

                self.assertEqual(dlg.message_box.get("1.0", tk.END).strip(), "")
                self.assertEqual(svc.count_pending_trello(), 1)
                self.assertIn("Pendenti Trello (1)", dlg.sync_trello_btn.cget("text"))

                ok_resp = MagicMock(status_code=201)
                ok_resp.json.return_value = {"id": "ui_c1", "shortUrl": "https://trello.com/c/uic1"}
                with patch("src.services.feedback_service.requests.post", return_value=ok_resp):
                    dlg._sync_pending()
                    sync_ok = self._pump_until(
                        lambda: "Sincronizzate 1 task" in dlg.status_lbl.cget("text")
                        and dlg.sync_trello_btn.cget("state") == "normal"
                    )
                    self.assertTrue(sync_ok, f"Status sync inatteso: {dlg.status_lbl.cget('text')!r}")

                self.assertEqual(svc.count_pending_trello(), 0)
                self.assertIn("Trello Allineato", dlg.sync_trello_btn.cget("text"))

                # Verifica nessun taglio/squish pulsanti nemmeno alla dimensione minima 640x580
                dlg.geometry("640x580")
                for _ in range(8):
                    self.root.update()
                    time.sleep(0.01)
                for btn in dlg.sync_trello_btn.master.winfo_children():
                    self.assertGreaterEqual(
                        btn.winfo_width(),
                        btn.winfo_reqwidth(),
                        f"Pulsante schiacciato in FeedbackDialogV3 (640x580): {btn.cget('text')!r}",
                    )
                # Verifica fallback errore apertura markdown/cartella e sync _cfg mutato
                with patch.object(svc, "open_markdown", return_value=False):
                    dlg._open_markdown()
                    self.assertIn("Impossibile aprire", dlg.status_lbl.cget("text"))
                with patch.object(svc, "open_folder", return_value=False):
                    dlg._open_folder()
                    self.assertIn("Impossibile aprire", dlg.status_lbl.cget("text"))
                dlg._cfg["trello_list_id"] = ""
                self.assertFalse(dlg.refresh_trello_state())
                self.assertIn("Solo Documento Locale", dlg.trello_badge.cget("text"))
            finally:
                try:
                    if dlg.winfo_exists():
                        dlg.destroy()
                except Exception:
                    pass

    def test_settings_dialog_feedback_tab_layout_submit_and_live_trello(self):
        """Verifica tab 💡 Consigli in SettingsDialogV3: tab bar, geometria, submit locale/Trello senza chiusura e reattività live."""
        if not self.root:
            self.skipTest("Tkinter non disponibile nell'ambiente di test")

        from src.config import load_app_config

        self.assertIs(FeedbackPanel, FeedbackPanelV3)

        with tempfile.TemporaryDirectory() as tmpdir:
            svc = FeedbackService(custom_dir=Path(tmpdir))
            saved_cfg = {}
            cfg = dict(load_app_config())
            cfg["trello_api_key"] = ""
            cfg["trello_token"] = ""
            cfg["trello_list_id"] = ""

            dlg = SettingsDialogV3(
                self.root,
                config=cfg,
                on_save=lambda vals: saved_cfg.update(vals),
                initial_tab="💡 Consigli",
                feedback_context={
                    "provider": "or_haiku_low",
                    "excel_file": "Colletta.xls",
                    "supermercato": "COOP TEST",
                    "gemini_api_key": "SHOULD_NOT_LEAK",
                },
                feedback_service=svc,
            )
            try:
                expected_tabs = [
                    "🔑 Chiavi API",
                    "🚀 Flusso IA & Modelli",
                    "🖨️ Scanner & Taratura",
                    "⌨️ Scorciatoie da Tastiera",
                    "💡 Consigli",
                ]
                self.assertEqual(list(dlg.tabs._tab_dict.keys()), expected_tabs)
                self.assertEqual(dlg.tabs.get(), "💡 Consigli")
                self.assertIsInstance(dlg.feedback_panel, FeedbackPanelV3)
                self.assertIs(dlg.tabs.tab("💡 Consigli"), dlg.feedback_panel.master)

                for _ in range(8):
                    self.root.update()
                    time.sleep(0.015)

                # Verifica dimensione finestra (~900x760), nessuna troncatura barra tab e altezze adeguate
                self.assertGreaterEqual(dlg.winfo_width(), 880)
                self.assertGreaterEqual(dlg.winfo_height(), 740)
                seg_req_w = dlg.tabs._segmented_button.winfo_reqwidth()
                tabs_w = dlg.tabs.winfo_width()
                self.assertGreater(seg_req_w, 200)
                self.assertLess(
                    seg_req_w,
                    tabs_w,
                    f"Barra dei 5 tab ({seg_req_w}px) sfora la larghezza di CTkTabview ({tabs_w}px)",
                )
                self.assertGreaterEqual(dlg.feedback_panel.message_box.winfo_height(), 90)
                self.assertGreaterEqual(dlg.feedback_panel.tasks_scroll._parent_canvas.winfo_height(), 170)

                # Nessun pulsante/label 'Config. Trello' visibile nel tab Consigli
                tab_texts = self._collect_widget_texts(dlg.feedback_panel)
                self.assertFalse(
                    any("Config. Trello" in t for t in tab_texts),
                    f"Il pulsante Config. Trello non deve essere visibile nel tab Consigli: {tab_texts!r}",
                )
                self.assertIn("Solo Documento Locale", dlg.feedback_panel.trello_badge.cget("text"))

                # Testo vuoto -> avviso con parent=dlg, nessun invio, dialogo aperto
                with patch("src.ui.components_dialogs.messagebox.showwarning") as mock_warn:
                    dlg.feedback_panel._submit()
                    mock_warn.assert_called_once()
                    self.assertEqual(mock_warn.call_args.kwargs.get("parent"), dlg)
                self.assertEqual(svc.list_local_feedback(), [])
                self.assertTrue(dlg.winfo_exists())

                # 1. Invio locale dal tab dentro SettingsDialogV3 (senza chiudere finestra né chiamare on_save)
                dlg.feedback_panel.author_var.set("Volontario Impostazioni")
                dlg.feedback_panel.message_box.insert("1.0", "Consiglio scritto dalle Impostazioni")
                dlg.feedback_panel._submit()
                ok_local = self._pump_until(
                    lambda: "documento locale" in dlg.feedback_panel.status_lbl.cget("text")
                    and dlg.feedback_panel.send_btn.cget("state") == "normal"
                )
                self.assertTrue(ok_local, f"Status inatteso: {dlg.feedback_panel.status_lbl.cget('text')!r}")
                self.assertTrue(dlg.winfo_exists(), "SettingsDialogV3 non deve chiudersi all'invio del consiglio")
                self.assertEqual(saved_cfg, {}, "L'invio del consiglio non deve invocare on_save delle Impostazioni")
                self.assertEqual(dlg.feedback_panel.message_box.get("1.0", tk.END).strip(), "")
                self.assertEqual(svc.count_pending_trello(), 1)
                self.assertIn("Pendenti Trello (1)", dlg.feedback_panel.sync_trello_btn.cget("text"))

                # Verifica nessun taglio/squish pulsanti né a 900x760 né alla minsize 840x640, e wraplength reattivo
                wrap_900 = int(dlg.feedback_panel.status_lbl.cget("wraplength"))
                for btn in dlg.feedback_panel.sync_trello_btn.master.winfo_children():
                    self.assertGreaterEqual(btn.winfo_width(), btn.winfo_reqwidth())

                dlg.geometry("840x640")
                for _ in range(8):
                    self.root.update()
                    time.sleep(0.01)
                wrap_840 = int(dlg.feedback_panel.status_lbl.cget("wraplength"))
                self.assertLess(wrap_840, wrap_900)
                for btn in dlg.feedback_panel.sync_trello_btn.master.winfo_children():
                    self.assertGreaterEqual(
                        btn.winfo_width(),
                        btn.winfo_reqwidth(),
                        f"Pulsante schiacciato in SettingsDialogV3 (840x640): {btn.cget('text')!r}",
                    )
                dlg.geometry("900x760")
                for _ in range(6):
                    self.root.update()
                    time.sleep(0.01)

                md_text = svc.get_markdown_path().read_text(encoding="utf-8")
                self.assertIn("Consiglio scritto dalle Impostazioni", md_text)
                self.assertIn("COOP TEST", md_text)
                self.assertNotIn("SHOULD_NOT_LEAK", md_text)

                # 2. Compilazione live delle variabili Trello aggiorna subito trello_badge e abilita invio Trello prima di _save()
                dlg.trello_api_key_var.set("live_k")
                dlg.trello_token_var.set("live_t")
                dlg.trello_list_id_var.set("live_l")
                self.assertIn("Trello Collegato", dlg.feedback_panel.trello_badge.cget("text"))

                resp_ok = MagicMock(status_code=201)
                resp_ok.json.return_value = {"id": "card_tab_1", "shortUrl": "https://trello.com/c/tab1"}
                with patch("src.services.feedback_service.requests.post", return_value=resp_ok) as mock_post:
                    dlg.feedback_panel.message_box.insert("1.0", "Secondo consiglio diretto Trello")
                    dlg.feedback_panel._submit()
                    ok_trello = self._pump_until(
                        lambda: "creata Card su Trello" in dlg.feedback_panel.status_lbl.cget("text")
                        and dlg.feedback_panel.send_btn.cget("state") == "normal"
                    )
                    self.assertTrue(ok_trello, f"Status Trello inatteso: {dlg.feedback_panel.status_lbl.cget('text')!r}")
                    mock_post.assert_called_once()
                    self.assertEqual(mock_post.call_args[1]["params"], {"key": "live_k", "token": "live_t"})
                    self.assertEqual(mock_post.call_args[1]["data"]["idList"], "live_l")

                self.assertTrue(dlg.winfo_exists())
                self.assertEqual(saved_cfg, {})

                # 3. Sync del primo item rimasto pendente + reset badge su rimozione list_id
                with patch("src.services.feedback_service.requests.post", return_value=resp_ok):
                    dlg.feedback_panel._sync_pending()
                    ok_sync = self._pump_until(
                        lambda: "Sincronizzate 1 task" in dlg.feedback_panel.status_lbl.cget("text")
                        and dlg.feedback_panel.sync_trello_btn.cget("state") == "normal"
                    )
                    self.assertTrue(ok_sync)
                self.assertEqual(svc.count_pending_trello(), 0)
                self.assertIn("Trello Allineato", dlg.feedback_panel.sync_trello_btn.cget("text"))

                dlg.trello_list_id_var.set("")
                self.assertIn("Solo Documento Locale", dlg.feedback_panel.trello_badge.cget("text"))
            finally:
                try:
                    if dlg.winfo_exists():
                        dlg.destroy()
                except Exception:
                    pass

            # Verifica fallback case-insensitive su initial_tab="consigli" e "feedback"
            for alias_tab in ("consigli", "feedback"):
                dlg_fb = SettingsDialogV3(
                    self.root,
                    config=cfg,
                    on_save=lambda _v: None,
                    initial_tab=alias_tab,
                    feedback_service=svc,
                )
                try:
                    self.assertEqual(dlg_fb.tabs.get(), "💡 Consigli")
                finally:
                    try:
                        if dlg_fb.winfo_exists():
                            dlg_fb.destroy()
                    except Exception:
                        pass

    def test_settings_dialog_close_while_feedback_submit_in_flight(self):
        """Chiudere SettingsDialogV3 durante l'invio asincrono non deve sollevare errori UI e salva il dato."""
        if not self.root:
            self.skipTest("Tkinter non disponibile nell'ambiente di test")

        import threading
        from src.config import load_app_config

        with tempfile.TemporaryDirectory() as tmpdir:
            svc = FeedbackService(custom_dir=Path(tmpdir))
            cfg = dict(load_app_config())
            cfg["trello_api_key"] = "k"
            cfg["trello_token"] = "t"
            cfg["trello_list_id"] = "l"

            started = threading.Event()
            release = threading.Event()

            def slow_post(*_args, **_kwargs):
                started.set()
                release.wait(timeout=2.0)
                r = MagicMock(status_code=201)
                r.json.return_value = {"id": "c_inflight", "shortUrl": "https://trello.com/c/inf"}
                return r

            dlg = SettingsDialogV3(
                self.root,
                config=cfg,
                on_save=lambda _v: None,
                initial_tab="💡 Consigli",
                feedback_service=svc,
            )
            with patch("src.services.feedback_service.requests.post", side_effect=slow_post):
                dlg.feedback_panel.message_box.insert("1.0", "Invio con chiusura immediata")
                dlg.feedback_panel._submit()
                self.assertTrue(started.wait(timeout=2.0))
                dlg.destroy()
                release.set()
                ok_saved = self._pump_until(lambda: len(svc.list_local_feedback()) == 1, timeout_s=3.0)
                self.assertTrue(ok_saved)
                self.assertTrue(svc.list_local_feedback()[0]["trello_synced"])


if __name__ == "__main__":
    unittest.main()
