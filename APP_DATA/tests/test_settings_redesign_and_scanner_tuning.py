"""Unit tests per la riprogettazione del pannello Impostazioni e laboratorio taratura scanner:



1. Verifica test chiavi API (Google Gemini su gemini-3.5-flash-lite e badge semplificato).



2. Verifica opzioni provider e mappa visiva dinamica CascadeVisualMap (v1, v2, provider singoli).



3. Verifica rilevamento scanner WIA (detect_scanners, get_scanner_status).



4. Verifica elaborazione e taratura immagine (apply_image_tuning, auto_optimize_scan).



5. Verifica alias di retrocompatibilità in components_dialogs.



"""







import sys



import unittest



from unittest.mock import MagicMock, patch



from pathlib import Path



from PIL import Image







sys.path.insert(0, str(Path(__file__).resolve().parent.parent))







from src.config import (



    SUPPORTED_PROVIDERS,



    DEFAULT_SCANNER_OPTIONS,



    DEFAULT_CASCADE_GEMINI_MODEL,



    DEFAULT_CASCADE_V2_PRIMARY_MODEL,



    DEFAULT_CASCADE_BUFFER_MODEL,



    DEFAULT_CASCADE_HAIKU_MODEL,



    DEFAULT_CASCADE_FREE_MODEL,



    DEFAULT_CASCADE_ARBITRATOR_MODEL,



    ARBITRATOR_DISPLAY_MAP,



    ARBITRATOR_INV_MAP,



    SUPPORTED_ARBITRATOR_MODELS,



    load_app_config,



)



from src.extractors.key_tester import test_gemini_api_key, test_openrouter_api_key



from src.scanner.scanner_service import (



    ScannerService,



    detect_scanners,



    get_scanner_status,



    apply_image_tuning,



)



from src.ui.components_dialogs import (



    SettingsDialog,



    SettingsDialogV3,



    CascadeVisualMap,



    CascadeVisualMapV3,



    AddApiKeyDialog,



    AddApiKeyDialogV3,



    BatchStrip,



    BatchStripV3,



    ScanProgressDialog,



    ScanProgressDialogV3,



    ContinueScanDialog,



    ContinueScanDialogV3,



    DuplicateRowDialog,



    DuplicateRowDialogV3,



)



from src.ui.app import BancoAlimentareApp, BancoAlimentareAppV3











class TestKeyTesterLite(unittest.TestCase):



    """Verifica che il test rapido della chiave Gemini utilizzi gemini-3.5-flash-lite con timeout appropriato."""







    @patch("requests.get")



    def test_gemini_key_tester_uses_flash_lite(self, mock_get):



        mock_resp = MagicMock()



        mock_resp.status_code = 200



        mock_get.return_value = mock_resp







        ok, msg = test_gemini_api_key("AIzaSyFakeKey123456789")



        self.assertTrue(ok)



        self.assertIn("valida", msg.lower())







        # Verifica URL e timeout



        mock_get.assert_called_once()



        call_url = mock_get.call_args[0][0]



        call_timeout = mock_get.call_args[1].get("timeout")



        self.assertIn("gemini-3.5-flash-lite", call_url)



        self.assertEqual(call_timeout, 10.0)







    @patch("requests.get")



    def test_gemini_key_tester_failure(self, mock_get):



        mock_resp = MagicMock()



        mock_resp.status_code = 400



        mock_resp.json.return_value = {"error": {"message": "API key not valid"}}



        mock_get.return_value = mock_resp







        ok, msg = test_gemini_api_key("AIzaSyInvalidKey")



        self.assertFalse(ok)



        self.assertIn("non valida", msg.lower())











class TestImageTuning(unittest.TestCase):



    """Verifica l'algoritmo di taratura immagine (luminosità, contrasto, nitidezza)."""







    def setUp(self):



        # Immagine di prova 100x100 grigia



        self.test_img = Image.new("RGB", (100, 100), color=(128, 128, 128))







    def test_apply_image_tuning_neutral(self):



        """Con valori neutri (1.0, 1.0, 1.0) l'immagine non viene alterata."""



        tuned = apply_image_tuning(self.test_img, brightness=1.0, contrast=1.0, sharpness=1.0)



        self.assertEqual(tuned.size, self.test_img.size)



        self.assertEqual(tuned.getpixel((50, 50)), (128, 128, 128))







    def test_apply_image_tuning_brightness_increase(self):



        """Aumentando la luminosità i pixel diventano più chiari."""



        tuned = apply_image_tuning(self.test_img, brightness=1.3, contrast=1.0, sharpness=1.0)



        r, g, b = tuned.getpixel((50, 50))



        self.assertGreater(r, 128)



        self.assertGreater(g, 128)



        self.assertGreater(b, 128)







    def test_apply_image_tuning_brightness_decrease(self):



        """Riducendo la luminosità i pixel diventano più scuri."""



        tuned = apply_image_tuning(self.test_img, brightness=0.7, contrast=1.0, sharpness=1.0)



        r, g, b = tuned.getpixel((50, 50))



        self.assertLess(r, 128)



        self.assertLess(g, 128)



        self.assertLess(b, 128)







    def test_apply_image_tuning_contrast_and_sharpness(self):



        """Verifica che contrasto e nitidezza vengano applicati senza errori su immagini RGB e Grayscale."""



        gray_img = Image.new("L", (80, 80), color=100)



        tuned_gray = apply_image_tuning(gray_img, brightness=1.1, contrast=1.5, sharpness=1.8)



        self.assertEqual(tuned_gray.mode, "RGB")



        self.assertEqual(tuned_gray.size, (80, 80))







    def test_auto_optimize_scan_with_tuning(self):



        """Verifica che auto_optimize_scan rispetti i parametri di taratura passati."""



        optimized = ScannerService.auto_optimize_scan(



            self.test_img,



            brightness=1.2,



            contrast=1.1,



            sharpness=1.1,



        )



        self.assertEqual(optimized.size, self.test_img.size)







    @patch("src.config.load_app_config")



    def test_auto_optimize_scan_pulls_from_config_when_none(self, mock_cfg):



        """Verifica che auto_optimize_scan legga automaticamente la taratura da config se non specificata."""



        mock_cfg.return_value = {



            "image_brightness": 1.4,



            "image_contrast": 1.2,



            "image_sharpness": 1.2,



        }



        optimized = ScannerService.auto_optimize_scan(self.test_img)



        self.assertEqual(optimized.size, self.test_img.size)



        r, g, b = optimized.getpixel((50, 50))



        # La luminosità 1.4 impostata in config deve aver schiarito i pixel rispetto al default 1.0



        self.assertGreater(r, 60)











class TestScannerHardwareDetection(unittest.TestCase):



    """Verifica il rilevamento WIA dello scanner."""







    def test_get_scanner_status_signature(self):



        """Verifica che get_scanner_status restituisca status, name e text."""



        info = get_scanner_status()



        self.assertIsInstance(info, dict)



        self.assertIn("status", info)



        self.assertIn(info["status"], ("ready", "busy", "none", "error"))



        self.assertIn("name", info)



        self.assertIn("text", info)







    def test_detect_scanners_structure(self):



        """Verifica che detect_scanners restituisca una lista di dizionari con chiavi nome e id."""



        scanners = detect_scanners()



        self.assertIsInstance(scanners, list)



        for s in scanners:



            self.assertIn("name", s)



            self.assertIn("id", s)







    def test_detect_scanners_multithread_safe(self):



        """Verifica che detect_scanners sia thread-safe e possa essere invocato da thread worker."""



        import threading



        errs = []



        res = []







        def worker():



            try:



                res.extend(detect_scanners())



            except Exception as e:



                errs.append(e)







        t = threading.Thread(target=worker)



        t.start()



        t.join(timeout=10.0)



        self.assertEqual(len(errs), 0)



        self.assertIsInstance(res, list)











class TestBackwardsCompatibilityAndAliases(unittest.TestCase):



    """Verifica che tutti gli alias di classe per retrocompatibilità siano corretti e identici."""







    def test_dialog_aliases(self):



        self.assertIs(SettingsDialogV3, SettingsDialog)



        self.assertIs(CascadeVisualMapV3, CascadeVisualMap)



        self.assertIs(AddApiKeyDialogV3, AddApiKeyDialog)



        self.assertIs(BatchStrip, BatchStripV3)



        self.assertIs(ScanProgressDialog, ScanProgressDialogV3)



        self.assertIs(ContinueScanDialog, ContinueScanDialogV3)



        self.assertIs(DuplicateRowDialog, DuplicateRowDialogV3)



        self.assertIs(BancoAlimentareApp, BancoAlimentareAppV3)







    def test_provider_mappings_consistency(self):



        """Verifica che SettingsDialog.PROVIDER_DISPLAY_MAP contenga tutti i provider supportati."""



        for prov in SUPPORTED_PROVIDERS:



            self.assertIn(prov, SettingsDialog.PROVIDER_DISPLAY_MAP)



            self.assertIn(prov, BancoAlimentareAppV3.PROVIDER_KEY_MAP)







    def test_default_scanner_options_contain_tuning_keys(self):



        """Verifica che le opzioni di default contengano luminosità, contrasto e nitidezza."""



        self.assertIn("image_brightness", DEFAULT_SCANNER_OPTIONS)



        self.assertIn("image_contrast", DEFAULT_SCANNER_OPTIONS)



        self.assertIn("image_sharpness", DEFAULT_SCANNER_OPTIONS)



        self.assertEqual(DEFAULT_SCANNER_OPTIONS["image_brightness"], 1.0)



        self.assertEqual(DEFAULT_SCANNER_OPTIONS["image_contrast"], 1.0)



class TestCascadeVisualMapAndDialogUI(unittest.TestCase):



    """Verifica l'interattività della mappa a cascata e il funzionamento di SettingsDialog."""







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







    def test_cascade_visual_map_modes(self):



        if not self.root:



            self.skipTest("Tkinter non disponibile nell'ambiente di test")







        cfg = load_app_config()



        cmap = CascadeVisualMap(self.root, config=cfg)







        # Test Cascade v1



        cmap.update_mode("cascade")



        self.assertIn("Cascata v1", cmap.capacity_lbl.cget("text"))







        # Test Cascade v2



        cmap.update_mode("cascade_v2")



        self.assertIn("Cascata v2", cmap.capacity_lbl.cget("text"))







        # Test Single Model (es. or_sonnet)



        cmap.update_mode("or_sonnet")



        self.assertIn("MODELLO SINGOLO", cmap.overall_badge.cget("text"))







    def test_settings_dialog_initialization_and_tuning_defaults(self):



        if not self.root:



            self.skipTest("Tkinter non disponibile nell'ambiente di test")







        cfg = load_app_config()
        cfg['image_brightness'] = 1.0
        cfg['image_contrast'] = 1.0
        cfg['image_sharpness'] = 1.0



        saved = {}







        def on_save(c):



            saved.update(c)







        dlg = SettingsDialog(self.root, config=cfg, on_save=on_save)



        dlg.withdraw()







        # Verifica valori slider taratura



        self.assertEqual(float(dlg.brightness_var.get()), 1.0)



        self.assertEqual(float(dlg.contrast_var.get()), 1.0)



        self.assertEqual(float(dlg.sharpness_var.get()), 1.0)







        # Modifica valori e reset ai predefiniti



        dlg.brightness_var.set(1.25)



        dlg.contrast_var.set(1.50)



        dlg.sharpness_var.set(2.00)



        dlg._reset_tuning_values()



        self.assertEqual(float(dlg.brightness_var.get()), 1.0)



        self.assertEqual(float(dlg.contrast_var.get()), 1.0)



        self.assertEqual(float(dlg.sharpness_var.get()), 1.0)







        # Test validazione scorciatoia non valida



        dlg._shortcut_vars["scan"].set("<TastoInesistenteXYZ>")



        with patch("tkinter.messagebox.showerror") as mock_err:



            dlg._save()



            mock_err.assert_called()







        dlg.destroy()







    def test_cascade_visual_map_key_preservation_on_mode_change(self):



        """Verifica che cambiare modalità con update_mode preservi le chiavi già configurate (non le azzeri a 0)."""



        if not self.root:



            self.skipTest("Tkinter non disponibile nell'ambiente di test")







        cfg = {



            "gemini_api_key": "AIzaSyFakeKey123",



            "openrouter_api_key": "sk-or-v1-fake",



        }



        cmap = CascadeVisualMap(self.root, config=cfg)



        self.assertIn("500", cmap.capacity_lbl.cget("text"))







        # Cambia modalità in cascade_v2 senza ri-passare le chiavi



        cmap.update_mode("cascade_v2")



        # Deve mantenere la capacità di 500 schede, non azzerarla a 0



        self.assertIn("500 schede/giorno", cmap.capacity_lbl.cget("text"))



        self.assertNotIn(" 0 schede/giorno", cmap.capacity_lbl.cget("text"))







    def test_provider_inv_map_exact_normalization(self):



        """Verifica che BancoAlimentareAppV3._normalize_display_to_provider mappi tutte le 8 stringhe visuali."""



        for key, disp in BancoAlimentareAppV3.PROVIDER_KEY_MAP.items():



            norm = BancoAlimentareAppV3._normalize_display_to_provider(disp)



            self.assertEqual(norm, key, f"Mappatura errata per {disp}: ottenuto {norm}, atteso {key}")











    def test_arbitrator_selection_and_persistence(self):



        """Verifica che la selezione del modello arbitro e lo switch vengano visualizzati e salvati in config."""



        if not self.root:



            self.skipTest("Tkinter non disponibile nell'ambiente di test")







        cfg = load_app_config()



        saved = {}







        def on_save(c):



            saved.update(c)







        dlg = SettingsDialog(self.root, config=cfg, on_save=on_save)



        dlg.withdraw()







        # Verifica presenza variabili arbitro



        self.assertTrue(hasattr(dlg, "arbitrator_model_var"))



        self.assertTrue(hasattr(dlg, "enable_arbitrator_var"))







        # Cambia modello arbitro in Luna e disattiva switch



        luna_disp = ARBITRATOR_DISPLAY_MAP["openai/gpt-6-luna"]



        dlg.arbitrator_model_var.set(luna_disp)



        dlg.enable_arbitrator_var.set(False)







        dlg._save()



        self.assertEqual(saved.get("cascade_arbitrator_model"), "openai/gpt-6-luna")



        self.assertEqual(saved.get("openrouter_arbitrator_model"), "openai/gpt-6-luna")



        self.assertFalse(saved.get("enable_auto_arbitrator"))







        dlg.destroy()







    def test_cascade_visual_map_arbitrator_dynamic_card(self):



        """Verifica che CascadeVisualMap mostri dinamicamente l'arbitro scelto (o stato disattivato)."""



        if not self.root:



            self.skipTest("Tkinter non disponibile nell'ambiente di test")







        cfg = {



            "gemini_api_key": "AIzaSyFakeKey123",



            "openrouter_api_key": "sk-or-v1-fake",



            "cascade_arbitrator_model": "anthropic/claude-haiku-5.5",



            "enable_auto_arbitrator": True,



        }



        cmap = CascadeVisualMap(self.root, config=cfg)



        



        def _extract_all_texts(w):
            res = []
            if hasattr(w, "cget"):
                try:
                    txt = w.cget("text")
                    if txt:
                        res.append(str(txt))
                except Exception:
                    pass
            if hasattr(w, "winfo_children"):
                for child in w.winfo_children():
                    res.extend(_extract_all_texts(child))
            return res

        # Con Haiku e chiave OR presente, la card arbitro deve mostrare Claude Haiku
        cards_text = " ".join(_extract_all_texts(cmap.cards_frame))
        self.assertIn("Haiku", cards_text)

        # Disattiva arbitro
        cmap.update_mode("cascade", arbitrator_model="anthropic/claude-haiku-5.5", enable_arbitrator=False)
        cards_text_dis = " ".join(_extract_all_texts(cmap.cards_frame))
        self.assertIn("Disattivato", cards_text_dis)












    def test_check_all_servers_status_logic(self):
        """Verifica che check_all_servers_status esegua il ping su Gemini e OpenRouter correttamente."""
        from src.extractors.key_tester import check_all_servers_status

        mock_gem_res = (True, "✓ Attiva · Chiave valida (risposta in 0.25s)")
        mock_or_res = (True, "✓ Attiva · Chiave valida (risposta in 0.40s)")

        with patch("src.extractors.key_tester.test_gemini_api_key", return_value=mock_gem_res):
            with patch("src.extractors.key_tester.test_openrouter_api_key", return_value=mock_or_res):
                res = check_all_servers_status(["GEM_KEY_1", "GEM_KEY_2"], "OR_KEY", timeout=2.0)
                self.assertTrue(res["gemini"]["ok"])
                self.assertIn("Operativo", res["gemini"]["status_text"])
                self.assertIn("Google 1", res["gemini"]["status_text"])
                self.assertIn("Google 2", res["gemini"]["status_text"])
                self.assertEqual(len(res["gemini"]["keys"]), 2)
                self.assertEqual(res["gemini"]["keys"][0]["index"], 1)
                self.assertEqual(res["gemini"]["keys"][1]["index"], 2)
                self.assertTrue(res["openrouter"]["ok"])
                self.assertIn("Operativo", res["openrouter"]["status_text"])

        # Con errori specifici (Timeout e Chiave non valida)
        side_effects_gem = [
            (False, "Timeout (4s): il server Google non risponde."),
            (False, "Chiave non valida o scaduta. API_KEY_INVALID"),
        ]
        with patch("src.extractors.key_tester.test_gemini_api_key", side_effect=side_effects_gem):
            with patch("src.extractors.key_tester.test_openrouter_api_key", return_value=(False, "Errore 401")):
                res_err = check_all_servers_status(["GEM_KEY_1", "GEM_KEY_2"], "", timeout=2.0)
                self.assertFalse(res_err["gemini"]["ok"])
                self.assertIn("Google 1: 🔴 Timeout", res_err["gemini"]["status_text"])
                self.assertIn("Google 2: 🔴 Non valida", res_err["gemini"]["status_text"])
                self.assertIn("⚪ Non configurato", res_err["openrouter"]["status_text"])

    def test_gemini_generation_logic(self):
        """Verifica test_gemini_generation per esito 200, 503, 429 e timeout."""
        from src.extractors.key_tester import test_gemini_generation
        import requests

        # Vuota
        ok, msg = test_gemini_generation("")
        self.assertFalse(ok)
        self.assertIn("vuota", msg.lower())

        # Successo 200
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "pong!"}]}}]
        }
        with patch("requests.post", return_value=mock_resp):
            ok, msg = test_gemini_generation("DUMMY_KEY", "gemini-3.5-flash-lite")
            self.assertTrue(ok)
            self.assertIn("pong!", msg)

        # Sovraccarico 503
        mock_resp_503 = MagicMock()
        mock_resp_503.status_code = 503
        with patch("requests.post", return_value=mock_resp_503):
            ok, msg = test_gemini_generation("DUMMY_KEY")
            self.assertFalse(ok)
            self.assertIn("503", msg)

        # Quota 429
        mock_resp_429 = MagicMock()
        mock_resp_429.status_code = 429
        with patch("requests.post", return_value=mock_resp_429):
            ok, msg = test_gemini_generation("DUMMY_KEY")
            self.assertFalse(ok)
            self.assertIn("429", msg)

        # Timeout
        with patch("requests.post", side_effect=requests.exceptions.Timeout):
            ok, msg = test_gemini_generation("DUMMY_KEY")
            self.assertFalse(ok)
            self.assertIn("Timeout", msg)

        # Content None / Safety finishReason
        mock_resp_safety = MagicMock()
        mock_resp_safety.status_code = 200
        mock_resp_safety.json.return_value = {
            "candidates": [{"finishReason": "SAFETY", "content": None}]
        }
        with patch("requests.post", return_value=mock_resp_safety):
            ok, msg = test_gemini_generation("DUMMY_KEY", "models/gemini-3.5-flash-lite")
            self.assertTrue(ok)
            self.assertIn("SAFETY", msg)

        # Prompt feedback block
        mock_resp_block = MagicMock()
        mock_resp_block.status_code = 200
        mock_resp_block.json.return_value = {
            "promptFeedback": {"blockReason": "SAFETY"}
        }
        with patch("requests.post", return_value=mock_resp_block):
            ok, msg = test_gemini_generation("DUMMY_KEY")
            self.assertFalse(ok)
            self.assertIn("sicurezza", msg.lower())

    def test_openrouter_generation_logic(self):
        """Verifica test_openrouter_generation per esito 200, 429, 401 ed errori in HTTP 200."""
        from src.extractors.key_tester import test_openrouter_generation

        # Vuota
        ok, msg = test_openrouter_generation("")
        self.assertFalse(ok)

        # 200 OK
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "pong free"}}]
        }
        with patch("requests.post", return_value=mock_resp):
            ok, msg = test_openrouter_generation("DUMMY_OR_KEY")
            self.assertTrue(ok)
            self.assertIn("pong free", msg)

        # 200 con errore nel JSON OpenRouter
        mock_resp_err = MagicMock()
        mock_resp_err.status_code = 200
        mock_resp_err.json.return_value = {
            "error": {"message": "Rate limit exceeded for free model", "code": 429}
        }
        with patch("requests.post", return_value=mock_resp_err):
            ok, msg = test_openrouter_generation("DUMMY_OR_KEY")
            self.assertFalse(ok)
            self.assertIn("Rate limit", msg)

        # 429
        mock_resp_429 = MagicMock()
        mock_resp_429.status_code = 429
        with patch("requests.post", return_value=mock_resp_429):
            ok, msg = test_openrouter_generation("DUMMY_OR_KEY")
            self.assertFalse(ok)
            self.assertIn("429", msg)

    def test_settings_dialog_collect_keys_and_ping_safety(self):
        """Verifica che _collect_keys_by_type restituisca Dict[str, List[str]] e non causi unpack error in _check_servers_status."""
        from src.ui.components_dialogs import SettingsDialogV3

        dialog = MagicMock()
        # Simula le righe di chiavi
        row1 = MagicMock()
        row1.provider_type = "gemini"
        row1.get.return_value = "AIzaSyKey1"
        row2 = MagicMock()
        row2.provider_type = "openrouter"
        row2.get.return_value = "sk-or-Key2"
        dialog._key_rows = [row1, row2]

        grouped = SettingsDialogV3._collect_keys_by_type(dialog)
        self.assertIsInstance(grouped, dict)
        self.assertEqual(grouped["gemini"], ["AIzaSyKey1"])
        self.assertEqual(grouped["openrouter"], ["sk-or-Key2"])

        # Verifica che estrarre le chiavi come list non causi ValueError
        gem_keys = list(grouped.get("gemini", []))
        or_keys = list(grouped.get("openrouter", []))
        self.assertEqual(gem_keys, ["AIzaSyKey1"])
        self.assertEqual(or_keys, ["sk-or-Key2"])



    def test_settings_dialog_fullscreen_toggle_and_dynamic_preview(self):
        """Verifica il toggle fullscreen/massimizza e il calcolo dinamico delle dimensioni anteprima."""
        from src.ui.components_dialogs import SettingsDialogV3
        from PIL import Image

        # Mock istanza dialog
        dialog = MagicMock()
        dialog._is_maximized = False
        dialog._state_val = "normal"
        dialog.state.side_effect = lambda *args: dialog._state_val if not args else setattr(dialog, "_state_val", args[0])
        dialog.attributes.return_value = False
        dialog._on_prev_container_resize = MagicMock()
        dialog._update_fullscreen_btn_text = MagicMock()

        # 1. Esegui toggle verso maximized
        SettingsDialogV3._toggle_fullscreen_or_maximize(dialog)
        self.assertTrue(dialog._is_maximized)
        self.assertEqual(dialog._state_val, "zoomed")
        dialog._update_fullscreen_btn_text.assert_called_with(True)

        # 2. Esegui toggle per ripristinare
        SettingsDialogV3._toggle_fullscreen_or_maximize(dialog)
        self.assertFalse(dialog._is_maximized)
        self.assertEqual(dialog._state_val, "normal")
        dialog._update_fullscreen_btn_text.assert_called_with(False)

        # 3. Test calcolo dinamico dimensione anteprima
        dialog.prev_cols = MagicMock()
        dialog.prev_cols.winfo_exists.return_value = True
        dialog.prev_cols.winfo_width.return_value = 1400
        dialog._test_pil_orig = Image.new("RGB", (2000, 1400), color="white")

        w, h = SettingsDialogV3._get_preview_target_size(dialog)
        self.assertGreaterEqual(w, 450)
        self.assertLessEqual(w, 850)
        self.assertGreaterEqual(h, 240)

    def test_settings_dialog_open_fullscreen_inspection(self):
        """Verifica che cliccare su ingrandisci apra FullScreenViewerDialogV3 con l'immagine appropriata."""
        from src.ui.components_dialogs import SettingsDialogV3
        from PIL import Image

        dialog = MagicMock()
        dialog._test_pil_orig = Image.new("RGB", (800, 600), color="white")
        dialog._last_test_image_path = "test_scheda.jpg"
        dialog.brightness_var.get.return_value = 1.1
        dialog.contrast_var.get.return_value = 1.2
        dialog.sharpness_var.get.return_value = 1.3
        dialog._safe_restore_modal = MagicMock()

        with patch("src.ui.components.FullScreenViewerDialogV3") as mock_fs:
            # Test apertura originale
            SettingsDialogV3._open_orig_fullscreen(dialog)
            mock_fs.assert_called_once()
            self.assertEqual(mock_fs.call_args[1]["initial_image"], dialog._test_pil_orig)

            # Test apertura filtrata
            mock_fs.reset_mock()
            SettingsDialogV3._open_tuned_fullscreen(dialog)
            mock_fs.assert_called_once()
            self.assertEqual(mock_fs.call_args[1]["image_path"], "Elaborata_con_Slider.jpg")
            self.assertIsNotNone(mock_fs.call_args[1]["initial_image"])

    def test_key_row_busy_buttons_management(self):
        """Verifica che _set_buttons_busy disabiliti tutti i bottoni e _reset_buttons_state ripristini i testi originali."""
        from src.ui.components_dialogs import _KeyRow

        row = MagicMock()
        row.test_btn = MagicMock()
        row.test_35_btn = MagicMock()
        row.test_38_btn = MagicMock()
        row.test_free_btn = MagicMock()

        # Disabilita per test 3.5
        _KeyRow._set_buttons_busy(row, "test_35_btn", "Attesa Gemini 3.5...")
        row.test_35_btn.configure.assert_called_with(state="disabled", text="Attesa Gemini 3.5...")
        row.test_38_btn.configure.assert_called_with(state="disabled")
        row.test_btn.configure.assert_called_with(state="disabled")
        row.test_free_btn.configure.assert_called_with(state="disabled")

        # Ripristina
        _KeyRow._reset_buttons_state(row)
        row.test_btn.configure.assert_called_with(state="normal", text="Testa Chiave")
        row.test_35_btn.configure.assert_called_with(state="normal", text="⚡ Richiesta Gemini 3.5")
        row.test_38_btn.configure.assert_called_with(state="normal", text="⚡ Richiesta Gemini 3.8")
        row.test_free_btn.configure.assert_called_with(state="normal", text="⚡ Richiesta Modello Free")


if __name__ == "__main__":



    unittest.main()



