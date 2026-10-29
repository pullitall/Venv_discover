"""Tests for themes, color palettes, DPI awareness, and dark title bar."""

from __future__ import annotations

import gc
import json
import os
import tempfile
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
import unittest
from unittest import mock

from tests import fakes
from tests.tk_support import DummyScanner, OFFSCREEN_GEOMETRY, create_test_app, create_test_root, make_offscreen
from venvdiscover.model import Env, Kind
from venvdiscover.places import Places
from venvdiscover.store import Settings, load_settings, save_settings
from venvdiscover.ui import theme
from venvdiscover.ui.app import App, CondaCleanDialog, DrivesDialog, SettingsDialog
from venvdiscover.ui.delete_dialog import ConfirmDeleteDialog
from venvdiscover.ui.envs_table import EnvsTable
from venvdiscover.ui.theme import (
    PALETTES,
    apply_theme,
    enable_dpi_awareness,
    is_windows_dark_mode,
    px,
    resolve_palette,
    resolve_theme,
    style_window,
)

# Literal pinned palettes: the reference colours for every palette and theme
LITERAL_PALETTES: dict[tuple[str, str], dict[str, str]] = {
    ("default", "dark"): {
        "bg": "#11110f",
        "panel": "#1a1a17",
        "line": "#2e2d29",
        "text": "#ece8dc",
        "muted": "#8f8a7e",
        "amber": "#ffb224",
        "amber_ink": "#241800",
        "selection": "#352d1b",
    },
    ("default", "light"): {
        "bg": "#f4f1e8",
        "panel": "#fbfaf5",
        "line": "#ddd8c8",
        "text": "#1d1c18",
        "muted": "#676358",
        "amber": "#985d00",
        "amber_ink": "#fff8e8",
        "selection": "#f9ebd7",
    },
    ("night-city", "dark"): {
        "bg": "#060810",
        "panel": "#0c101c",
        "line": "#1e263d",
        "text": "#e2e8f0",
        "muted": "#8091a7",
        "amber": "#00f0ff",
        "amber_ink": "#041017",
        "selection": "#0b2b37",
    },
    ("night-city", "light"): {
        "bg": "#f0f4f8",
        "panel": "#ffffff",
        "line": "#cbd5e1",
        "text": "#0f172a",
        "muted": "#475569",
        "amber": "#026aa2",
        "amber_ink": "#ffffff",
        "selection": "#e6f0f6",
    },
    ("calm-glass", "dark"): {
        "bg": "#0d1117",
        "panel": "#161b22",
        "line": "#30363d",
        "text": "#f0f6fc",
        "muted": "#9ba5b1",
        "amber": "#e3b341",
        "amber_ink": "#161b22",
        "selection": "#333026",
    },
    ("calm-glass", "light"): {
        "bg": "#f6f8fa",
        "panel": "#ffffff",
        "line": "#d0d7de",
        "text": "#1f2328",
        "muted": "#4d5560",
        "amber": "#8c5d00",
        "amber_ink": "#ffffff",
        "selection": "#f2ebd7",
    },
    ("bold-editorial", "dark"): {
        "bg": "#0a0a0c",
        "panel": "#141418",
        "line": "#2c2c36",
        "text": "#fbfbfe",
        "muted": "#9a9aaf",
        "amber": "#f59e0b",
        "amber_ink": "#141418",
        "selection": "#342816",
    },
    ("bold-editorial", "light"): {
        "bg": "#f5f5f7",
        "panel": "#ffffff",
        "line": "#d0d0dc",
        "text": "#0a0a0e",
        "muted": "#4a4a58",
        "amber": "#9a4505",
        "amber_ink": "#ffffff",
        "selection": "#f5eae5",
    },
}


class UIThemeTests(unittest.TestCase):
    def test_theme_colours_applied_pinned(self) -> None:
        """For each theme x palette, apply_theme matches literal pinned CSS constants."""
        root = create_test_root()
        try:
            style = ttk.Style(root)
            palettes = ["default", "night-city", "calm-glass", "bold-editorial"]
            themes = ["dark", "light"]

            for pal in palettes:
                for th in themes:
                    with self.subTest(palette=pal, theme=th):
                        apply_theme(root, th, pal)
                        pinned = LITERAL_PALETTES[(pal, th)]
                        expected_panel = pinned["panel"]
                        expected_sel = pinned["selection"]

                        # Lookup background
                        actual_bg = style.lookup("Treeview", "background")
                        self.assertEqual(
                            actual_bg.lower(),
                            expected_panel.lower(),
                            f"Treeview background mismatch for {th}/{pal}",
                        )

                        # Selection map
                        bg_map = style.map("Treeview", query_opt="background")
                        sel_colors = [col for state, col in bg_map if state == "selected"]
                        self.assertTrue(sel_colors, f"No selection background map for {th}/{pal}")
                        self.assertEqual(
                            sel_colors[0].lower(),
                            expected_sel.lower(),
                            f"Treeview selection background mismatch for {th}/{pal}",
                        )
        finally:
            root.destroy()

    def test_theme_switch_live(self) -> None:
        """Switching palette and theme in Settings window restyles App and saves settings without losing drives or terminal."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            initial_settings = Settings(
                drives=["C:\\", "D:\\"],
                terminal="powershell",
                theme="dark",
                palette="default",
            )
            save_settings(places.settings_path, initial_settings)

            app = create_test_app(places)
            try:
                # Open SettingsDialog
                dialog = SettingsDialog(app)
                make_offscreen(dialog)

                # Switch palette to Night city
                dialog.palette_var.set("Night city")
                dialog._on_palette_changed()

                # Verify live app active settings and style
                self.assertEqual(app.current_palette, "night-city")
                style = ttk.Style(app)
                self.assertEqual(
                    style.lookup("Treeview", "background").lower(),
                    LITERAL_PALETTES[("night-city", "dark")]["panel"].lower(),
                )

                # Verify settings persisted on disk keeping drives and terminal
                saved = load_settings(places.settings_path)
                self.assertEqual(saved.drives, ["C:\\", "D:\\"])
                self.assertEqual(saved.terminal, "powershell")
                self.assertEqual(saved.theme, "dark")
                self.assertEqual(saved.palette, "night-city")

                # Switch theme to Light
                dialog.theme_var.set("Light")
                dialog._on_theme_changed()

                self.assertEqual(app.current_theme, "light")
                self.assertEqual(
                    style.lookup("Treeview", "background").lower(),
                    LITERAL_PALETTES[("night-city", "light")]["panel"].lower(),
                )

                saved2 = load_settings(places.settings_path)
                self.assertEqual(saved2.drives, ["C:\\", "D:\\"])
                self.assertEqual(saved2.terminal, "powershell")
                self.assertEqual(saved2.theme, "light")
                self.assertEqual(saved2.palette, "night-city")

                dialog.destroy()
            finally:
                app.destroy()

    def test_title_bar_dwm_attribute(self) -> None:
        """set_title_bar_dark_mode calls DwmSetWindowAttribute for attributes 20, 35, 36 with expected COLORREFs."""
        root = create_test_root()
        try:
            with mock.patch("ctypes.windll.dwmapi.DwmSetWindowAttribute") as mock_dwm:
                with mock.patch("ctypes.windll.user32.SetWindowPos") as mock_swp:
                    # Dark mode with bg and text colors
                    theme.set_title_bar_dark_mode(root, True, bg_color="#11110f", text_color="#ece8dc")
                    self.assertEqual(mock_dwm.call_count, 3)
                    # Attribute 20: DWMWA_USE_IMMERSIVE_DARK_MODE
                    call_20 = [c[0] for c in mock_dwm.call_args_list if c[0][1] == 20][0]
                    self.assertEqual(call_20[2]._obj.value, 1)
                    # Attribute 35: DWMWA_CAPTION_COLOR (0x00BBGGRR: #11110f -> 0x000f1111)
                    call_35 = [c[0] for c in mock_dwm.call_args_list if c[0][1] == 35][0]
                    self.assertEqual(call_35[2]._obj.value, 0x000f1111)
                    # Attribute 36: DWMWA_TEXT_COLOR (0x00BBGGRR: #ece8dc -> 0x00dce8ec)
                    call_36 = [c[0] for c in mock_dwm.call_args_list if c[0][1] == 36][0]
                    self.assertEqual(call_36[2]._obj.value, 0x00dce8ec)

                    mock_swp.assert_called_once()
                    self.assertEqual(mock_swp.call_args[0][6], 0x0037)

                    mock_dwm.reset_mock()
                    mock_swp.reset_mock()
                    # Light mode with bg and text colors
                    theme.set_title_bar_dark_mode(root, False, bg_color="#f4f1e8", text_color="#1d1c18")
                    self.assertEqual(mock_dwm.call_count, 3)
                    call_20 = [c[0] for c in mock_dwm.call_args_list if c[0][1] == 20][0]
                    self.assertEqual(call_20[2]._obj.value, 0)
                    # Attribute 35: #f4f1e8 -> 0x00e8f1f4
                    call_35 = [c[0] for c in mock_dwm.call_args_list if c[0][1] == 35][0]
                    self.assertEqual(call_35[2]._obj.value, 0x00e8f1f4)
                    # Attribute 36: #1d1c18 -> 0x00181c1d
                    call_36 = [c[0] for c in mock_dwm.call_args_list if c[0][1] == 36][0]
                    self.assertEqual(call_36[2]._obj.value, 0x00181c1d)

                    # Also verify apply_theme propagates palette bg/text to title bar
                    mock_dwm.reset_mock()
                    with mock.patch.object(root, "winfo_ismapped", return_value=True):
                        theme.apply_theme(root, "light", "default")
                        call_20 = [c[0] for c in mock_dwm.call_args_list if c[0][1] == 20][-1]
                        self.assertEqual(call_20[2]._obj.value, 0)
                        call_35 = [c[0] for c in mock_dwm.call_args_list if c[0][1] == 35][-1]
                        self.assertEqual(call_35[2]._obj.value, 0x00e8f1f4)
                        call_36 = [c[0] for c in mock_dwm.call_args_list if c[0][1] == 36][-1]
                        self.assertEqual(call_36[2]._obj.value, 0x00181c1d)
        finally:
            root.destroy()

    def test_title_bar_map_binding_no_stale_dark(self) -> None:
        """After live switch to Light, Map event on child does not re-apply title bar, and Map on root applies Light."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            app = create_test_app(places)
            try:
                with mock.patch("ctypes.windll.dwmapi.DwmSetWindowAttribute") as mock_dwm:
                    with mock.patch("ctypes.windll.user32.SetWindowPos"):
                        theme.apply_theme(app, "light", "default")
                        cnt_before = len([c for c in mock_dwm.call_args_list if c[0][1] == 20])

                        # Map on a child widget must NOT trigger title bar set
                        app.envs_table.event_generate("<Map>")
                        app.update()
                        cnt_child = len([c for c in mock_dwm.call_args_list if c[0][1] == 20])
                        self.assertEqual(cnt_child, cnt_before)

                        # Map on root must apply current theme (light, dark=False, caption #f4f1e8)
                        app.event_generate("<Map>")
                        app.update()
                        calls_20 = [c for c in mock_dwm.call_args_list if c[0][1] == 20]
                        self.assertGreater(len(calls_20), cnt_before)
                        self.assertEqual(calls_20[-1][0][2]._obj.value, 0)

                        calls_35 = [c for c in mock_dwm.call_args_list if c[0][1] == 35]
                        self.assertEqual(calls_35[-1][0][2]._obj.value, 0x00e8f1f4)
            finally:
                app.destroy()

    def test_render_rounded_box_png_call_count(self) -> None:
        """Deterministic test: <= 26 render_rounded_box_png calls on startup, 0 on re-applying current palette."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            with mock.patch("venvdiscover.ui.theme.render_rounded_box_png", wraps=theme.render_rounded_box_png) as mock_render:
                app = create_test_app(places)
                try:
                    startup_count = mock_render.call_count
                    self.assertLessEqual(startup_count, 26)

                    mock_render.reset_mock()
                    theme.apply_theme(app, app.current_theme, app.current_palette)
                    self.assertEqual(mock_render.call_count, 0)
                    app.update_idletasks()
                finally:
                    app.destroy()

    def test_saved_theme_loaded_at_startup(self) -> None:
        """When settings have light and calm-glass, App starts with Treeview background set to calm-glass light panel (#ffffff)."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            save_settings(places.settings_path, Settings(theme="light", palette="calm-glass"))
            app = create_test_app(places)
            try:
                style = ttk.Style(app)
                bg = style.lookup("Treeview", "background")
                self.assertEqual(bg.lower(), "#ffffff")
                self.assertEqual(app.current_theme, "light")
                self.assertEqual(app.current_palette, "calm-glass")
            finally:
                app.destroy()

    def test_envs_count_label_updates(self) -> None:
        """The 'ENVIRONMENTS · N' section label updates on env added, gone, and deleted."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            app = create_test_app(places)
            try:
                self.assertEqual(app.envs_section_label.cget("text"), "ENVIRONMENTS \u00b7 0")
                env1 = Env(path=os.path.join(tmp, "e1"), kind=Kind.VENV, name="e1")
                env2 = Env(path=os.path.join(tmp, "e2"), kind=Kind.VENV, name="e2")

                app.envs[env1.key] = env1
                app.envs_table.upsert(env1)
                app._update_envs_count()
                self.assertEqual(app.envs_section_label.cget("text"), "ENVIRONMENTS \u00b7 1")

                app.envs[env2.key] = env2
                app.envs_table.upsert(env2)
                app._update_envs_count()
                self.assertEqual(app.envs_section_label.cget("text"), "ENVIRONMENTS \u00b7 2")

                app.envs.pop(env1.key, None)
                app.envs_table.remove(env1.key)
                app._update_envs_count()
                self.assertEqual(app.envs_section_label.cget("text"), "ENVIRONMENTS \u00b7 1")
            finally:
                app.destroy()

    def test_button_styles(self) -> None:
        """Open terminal button uses Accent.TButton and Delete button uses Danger.TButton."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            app = create_test_app(places)
            try:
                self.assertIn(app.btn_terminal.cget("style"), ("Accent.TButton", "SplitLeft.Accent.TButton"))
                self.assertEqual(app.btn_delete.cget("style"), "Danger.TButton")
            finally:
                app.destroy()

    def test_named_fonts_persist_after_gc(self) -> None:
        """After apply_theme and gc.collect(), named fonts remain in tkfont.names() with Segoe UI family."""
        root = create_test_root()
        try:
            apply_theme(root, "dark", "default")
            gc.collect()

            names = tkfont.names(root=root)
            self.assertIn("VDHeading", names)
            self.assertIn("VDSection", names)
            self.assertIn("VDDefault", names)

            h_actual = tkfont.Font(root=root, name="VDHeading", exists=True).actual()
            self.assertIn(h_actual["family"], ("Segoe UI Semibold", "Segoe UI Variable Text Semibold", "Segoe UI Variable Display Semibold", "Segoe UI"))

            s_actual = tkfont.Font(root=root, name="VDSection", exists=True).actual()
            self.assertIn(s_actual["family"], ("Segoe UI Semibold", "Segoe UI Variable Text Semibold", "Segoe UI Variable Display Semibold", "Segoe UI"))

            d_actual = tkfont.Font(root=root, name="VDDefault", exists=True).actual()
            self.assertIn(d_actual["family"], ("Segoe UI", "Segoe UI Variable Text"))
        finally:
            root.destroy()

    def test_scaling_at_150_percent(self) -> None:
        """When winfo_fpixels('1i') returns 144, px() scales by 1.5, geometry is 1650x975, and columns scale."""
        root = create_test_root()
        try:
            with mock.patch.object(root, "winfo_fpixels", return_value=144.0):
                self.assertEqual(px(root, 1100), 1650)
                self.assertEqual(px(root, 650), 975)
                self.assertEqual(px(root, 100), 150)
                self.assertEqual(px(root, 10), 15)
        finally:
            root.destroy()

    def test_no_light_grey_leftovers_in_dark_mode(self) -> None:
        """In dark mode for all palettes, no style looks up to clam's default light-grey colors."""
        root = create_test_root()
        try:
            leftovers = {"#9e9a91", "#eeebe7", "#cfcdc8", "#dcdad5"}
            styles = [
                "TNotebook",
                "TNotebook.Tab",
                "TPanedwindow",
                "Sash",
                "TScrollbar",
                "Treeview",
                "TEntry",
                "TCombobox",
                "TButton",
            ]
            options = ["background", "bordercolor", "lightcolor", "darkcolor", "troughcolor", "fieldbackground"]
            palettes = ["default", "night-city", "calm-glass", "bold-editorial"]

            style = ttk.Style(root)
            for pal in palettes:
                apply_theme(root, "dark", pal)
                for s in styles:
                    for o in options:
                        val = style.lookup(s, o)
                        if val:
                            self.assertNotIn(
                                str(val).lower(),
                                leftovers,
                                f"Light-grey leftover {val} found in style '{s}' opt '{o}' for palette '{pal}'",
                            )
        finally:
            root.destroy()

    def test_dialogs_themed_in_dark_mode(self) -> None:
        """Delete, Conda clean, Drives, and Settings dialogs open in dark mode with Toplevel bg = bg and Text bg = panel."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            app = create_test_app(places)
            try:
                apply_theme(app, "dark", "default")
                dark_colors = LITERAL_PALETTES[("default", "dark")]
                expected_bg = dark_colors["bg"].lower()
                expected_panel = dark_colors["panel"].lower()

                # 1. DrivesDialog
                dd = DrivesDialog(app)
                make_offscreen(dd)
                self.assertEqual(dd.cget("background").lower(), expected_bg)
                dd.destroy()

                # 2. SettingsDialog
                sd = SettingsDialog(app)
                make_offscreen(sd)
                self.assertEqual(sd.cget("background").lower(), expected_bg)
                sd.destroy()

                # 3. CondaCleanDialog
                cd = CondaCleanDialog(app)
                make_offscreen(cd)
                self.assertEqual(cd.cget("background").lower(), expected_bg)
                self.assertEqual(cd.text.cget("background").lower(), expected_panel)
                cd.destroy()

                # 4. ConfirmDeleteDialog
                env1 = Env(path=os.path.join(tmp, "env1"), kind=Kind.VENV, name="env1")
                del_dlg = ConfirmDeleteDialog(
                    app,
                    to_bin=[env1],
                    permanent=[],
                    locked=[],
                    extras={},
                    on_confirm=lambda: None,
                )
                make_offscreen(del_dlg)
                self.assertEqual(del_dlg.cget("background").lower(), expected_bg)
                self.assertEqual(del_dlg.text_widget.cget("background").lower(), expected_panel)
                del_dlg.destroy()
            finally:
                app.destroy()

    def test_match_windows(self) -> None:
        """Match Windows reads registry and picks dark when 0, light when 1."""
        with mock.patch("venvdiscover.ui.theme.is_windows_dark_mode", return_value=False):
            self.assertEqual(resolve_theme("Match Windows"), "light")
            self.assertEqual(resolve_theme("match windows"), "light")

        with mock.patch("venvdiscover.ui.theme.is_windows_dark_mode", return_value=True):
            self.assertEqual(resolve_theme("Match Windows"), "dark")
            self.assertEqual(resolve_theme("match windows"), "dark")

    def test_stripes_keep_temp(self) -> None:
        """Even/odd stripe tags and the temp tag are both on a temp row."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            app = create_test_app(places)
            try:
                env_reg = Env(
                    path=os.path.join(tmp, "env_normal"),
                    kind=Kind.VENV,
                    name="env_normal",
                )
                env_temp = Env(
                    path=os.path.join(tmp, "env_temp"),
                    kind=Kind.TEMP_VENV,
                    name="env_temp",
                )

                app.envs_table.upsert(env_reg)
                app.envs_table.upsert(env_temp)

                temp_tags = app.envs_table.tree.item(env_temp.key, "tags")
                self.assertIn("temp", temp_tags)
                self.assertTrue("even" in temp_tags or "odd" in temp_tags)

                reg_tags = app.envs_table.tree.item(env_reg.key, "tags")
                self.assertNotIn("temp", reg_tags)
                self.assertTrue("even" in reg_tags or "odd" in reg_tags)
            finally:
                app.destroy()

    def test_dpi_only_in_main(self) -> None:
        """Building App never calls SetProcessDpiAwareness, and main() calls it before creating window."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)

            call_order = []
            mock_shcore = mock.MagicMock()
            mock_shcore.SetProcessDpiAwareness.side_effect = lambda level: call_order.append(("dpi", level))

            class FakeApp:
                def __init__(self, *args, **kwargs) -> None:
                    call_order.append("app")

                def mainloop(self) -> None:
                    call_order.append("mainloop")

            # 1. Building App directly must not call SetProcessDpiAwareness
            with mock.patch("ctypes.windll.shcore", mock_shcore):
                app = create_test_app(places)
                try:
                    mock_shcore.SetProcessDpiAwareness.assert_not_called()
                finally:
                    app.destroy()

            # 2. main() calls SetProcessDpiAwareness before creating App
            with mock.patch("ctypes.windll.shcore", mock_shcore):
                with mock.patch("venvdiscover.ui.app.App", FakeApp):
                    with mock.patch("venvdiscover.places.Places.from_environ", return_value=places):
                        from venvdiscover.ui.app import main

                        main()

            mock_shcore.SetProcessDpiAwareness.assert_called_once_with(2)
            self.assertEqual(call_order, [("dpi", 2), "app", "mainloop"])


    def test_image_elements_persist_after_gc(self) -> None:
        """All Mockup B PhotoImage element images remain referenced on root after GC."""
        root = create_test_root()
        try:
            apply_theme(root, "dark", "default")
            gc.collect()
            imgs = getattr(root, "_vd_element_images", None)
            self.assertIsNotNone(imgs)
            self.assertIn("btn_norm", imgs)
            self.assertIn("acc_norm", imgs)
            self.assertIn("d_norm", imgs)
            self.assertIn("card_bg", imgs)
            self.assertIn("sb_v_norm", imgs)
            self.assertIn("sb_h_norm", imgs)
            self.assertIn("e_norm", imgs)
        finally:
            root.destroy()

    def test_palette_switch_rebuilds_images(self) -> None:
        """Switching palette updates button background image pixel colors."""
        root = create_test_root()
        try:
            apply_theme(root, "dark", "default")
            imgs = getattr(root, "_vd_element_images", {})
            btn_img = imgs["btn_norm"]
            r1, g1, b1 = btn_img.get(15, 15)

            apply_theme(root, "light", "default")
            imgs2 = getattr(root, "_vd_element_images", {})
            btn_img2 = imgs2["btn_norm"]
            r2, g2, b2 = btn_img2.get(15, 15)
            self.assertNotEqual((r1, g1, b1), (r2, g2, b2))
        finally:
            root.destroy()

    def test_scrollbar_layout_has_no_arrows(self) -> None:
        """Vertical and horizontal scrollbars have zero arrows in their ttk layout."""
        root = create_test_root()
        try:
            apply_theme(root, "dark", "default")
            style = ttk.Style(root)
            v_layout = str(style.layout("Vertical.TScrollbar"))
            h_layout = str(style.layout("Horizontal.TScrollbar"))
            self.assertNotIn("uparrow", v_layout.lower())
            self.assertNotIn("downarrow", v_layout.lower())
            self.assertNotIn("leftarrow", h_layout.lower())
            self.assertNotIn("rightarrow", h_layout.lower())
        finally:
            root.destroy()

    def test_scrollbar_layouts_name_a_thumb(self) -> None:
        """Tk finds the slider by name: each scrollbar layout needs an element ending in '.thumb'."""
        root = create_test_root()
        try:
            apply_theme(root, "dark", "default")
            style = ttk.Style(root)

            def names(layout):
                for name, opts in layout:
                    yield name
                    yield from names(opts.get("children", []))

            for layout_name in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
                with self.subTest(layout=layout_name):
                    found = [n for n in names(style.layout(layout_name)) if n.endswith(".thumb")]
                    self.assertTrue(found, f"{layout_name} has no *.thumb element: {style.layout(layout_name)}")
        finally:
            root.destroy()

    def test_scrollbar_thumb_follows_the_view(self) -> None:
        """Scrolled to the start, middle and end, the slider sits at the start, middle and end of the bar."""
        root = create_test_root(mapped=True)
        try:
            root.geometry(f"320x220{OFFSCREEN_GEOMETRY}")
            apply_theme(root, "dark", "default")
            tree = ttk.Treeview(root, columns=("a",), show="headings", height=6)
            vsb = ttk.Scrollbar(root, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=vsb.set)
            for i in range(200):
                tree.insert("", "end", values=(f"row {i}",))
            text = tk.Text(root, wrap="none", height=2, width=20)
            hsb = ttk.Scrollbar(root, orient="horizontal", command=text.xview)
            text.configure(xscrollcommand=hsb.set)
            text.insert("1.0", "x" * 2000)
            tree.grid(row=0, column=0, sticky="nsew")
            vsb.grid(row=0, column=1, sticky="ns")
            text.grid(row=1, column=0, sticky="ew")
            hsb.grid(row=2, column=0, sticky="ew")
            root.update()

            def ends(sb: ttk.Scrollbar, vertical: bool) -> tuple[str, str]:
                if vertical:
                    x, h = sb.winfo_width() // 2, sb.winfo_height()
                    return sb.identify(x, 2), sb.identify(x, h - 3)
                y, w = sb.winfo_height() // 2, sb.winfo_width()
                return sb.identify(2, y), sb.identify(w - 3, y)

            for sb, view, vertical in ((vsb, tree.yview_moveto, True), (hsb, text.xview_moveto, False)):
                for where, expect in ((0.0, (True, False)), (0.5, (False, False)), (1.0, (False, True))):
                    with self.subTest(vertical=vertical, where=where):
                        view(where)
                        root.update()
                        first, last = ends(sb, vertical)
                        self.assertEqual((first.endswith("thumb"), last.endswith("thumb")), expect, (first, last))
        finally:
            root.destroy()

    def test_scrollbar_orange_in_default_light(self) -> None:
        """In default light amber mode, scrollbar thumbs are rendered in orange."""
        root = create_test_root()
        try:
            apply_theme(root, "light", "default")
            imgs = getattr(root, "_vd_element_images", {})
            self.assertIn("sb_v_norm", imgs)
            sb_v = imgs["sb_v_norm"]
            r, g, b = sb_v.get(4, 10)
            self.assertGreater(r, 200)
            self.assertGreater(g, 90)
            self.assertLess(b, 50)
        finally:
            root.destroy()

    def test_card_image_center_at_least_128(self) -> None:
        """Every image used by VD.CardBg has a centre of at least 128 px in both directions."""
        root = create_test_root()
        try:
            apply_theme(root, "dark", "default")
            imgs = getattr(root, "_vd_element_images", {})
            self.assertIn("card_bg", imgs)
            card_img = imgs["card_bg"]
            # Border is 11, so center slice is (width - 22, height - 22)
            center_w = card_img.width() - 22
            center_h = card_img.height() - 22
            self.assertGreaterEqual(center_w, 128, f"Card center width {center_w} < 128")
            self.assertGreaterEqual(center_h, 128, f"Card center height {center_h} < 128")
        finally:
            root.destroy()

    def test_no_frame_inside_card_uses_card_tframe(self) -> None:
        """No frame inside a card uses Card.TFrame to prevent nested image tiling."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            app = create_test_app(places, mapped=True)
            try:
                def check_descendants(widget):
                    for child in widget.winfo_children():
                        if isinstance(child, ttk.Frame):
                            style_name = child.cget("style")
                            self.assertNotEqual(
                                style_name,
                                "Card.TFrame",
                                f"Child frame {child} inside card uses Card.TFrame",
                            )
                        check_descendants(child)

                check_descendants(app.left_card)
                check_descendants(app.right_card)
            finally:
                app.destroy()

    def test_fixed_columns_cover_widest_values_and_headings(self) -> None:
        """Every fixed column is >= measure() of its widest real value and its heading fits."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            app = create_test_app(places, mapped=True)
            try:
                tree = app.envs_table.tree
                f_def = tkfont.nametofont("VDDefault")
                f_head = tkfont.nametofont("VDHeading")

                # Widest real values:
                # Kind: 'python install (locked)'
                # Size: '999.9 MB'
                # Last used: '2026-10-27'
                # Python: '3.10.18'
                # Packages: heading 'Packages'
                expected_min = {
                    "kind": max(f_def.measure("python install (locked)"), f_head.measure("Kind")),
                    "size": max(f_def.measure("999.9 MB"), f_head.measure("Size")),
                    "last_used": max(f_def.measure("2026-10-27"), f_head.measure("Last used")),
                    "python": max(f_def.measure("3.10.18"), f_head.measure("Python")),
                    "packages": max(f_def.measure("999"), f_head.measure("Packages")),
                    "name": f_head.measure("Name"),
                }

                for col, min_measure in expected_min.items():
                    col_w = tree.column(col, "width")
                    self.assertGreaterEqual(
                        col_w,
                        min_measure,
                        f"Column '{col}' width {col_w} is smaller than measured text/heading {min_measure}",
                    )

                # #0 kind dot has room for dot plus a gap
                dot_w = tree.column("#0", "width")
                self.assertGreaterEqual(dot_w, 24, f"#0 dot column width {dot_w} is too small (<24px)")
            finally:
                app.destroy()

    def test_location_column_at_least_200px_on_standard_screen(self) -> None:
        """At default size on a 1707x1067 logical screen, Location column has >= 200 px."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            with mock.patch.object(tk.Tk, "winfo_screenwidth", return_value=1707), \
                 mock.patch.object(tk.Tk, "winfo_screenheight", return_value=1067):
                app = create_test_app(places, mapped=True)
                try:
                    # Tk caps a window at the real screen size, which the patched winfo_screen* don't change
                    # (CI runners have small screens), so the default-size layout can only be measured here
                    # when the real screen fits the default window.
                    if app.maxsize()[0] < px(app, 1280):
                        self.skipTest(f"real screen too narrow for the default window (maxsize {app.maxsize()})")
                    app.update_idletasks()
                    app._adjust_initial_sash()
                    app.update_idletasks()
                    app.update()

                    tree = app.envs_table.tree
                    fixed_cols = ("#0", "name", "kind", "python", "packages", "size", "last_used")
                    fixed_sum = sum(tree.column(c, "width") for c in fixed_cols)
                    tree_width = tree.winfo_width()
                    remaining_for_location = tree_width - fixed_sum

                    self.assertGreaterEqual(
                        remaining_for_location,
                        200,
                        f"Expected >= 200 logical px for Location, got {remaining_for_location} (tree_width={tree_width}, fixed_sum={fixed_sum})",
                    )
                finally:
                    app.destroy()

    def test_minsize_covers_action_bar_width(self) -> None:
        """minsize()[0] >= action bar winfo_reqwidth() after the first idle."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            app = create_test_app(places, mapped=True)
            try:
                app.update_idletasks()
                app.update()
                min_w, min_h = app.minsize()
                req_w = app.actions_bar.winfo_reqwidth()
                self.assertGreaterEqual(
                    min_w,
                    req_w,
                    f"Expected minsize width ({min_w}) >= actions bar reqwidth ({req_w})",
                )
            finally:
                app.destroy()

    def test_resolve_palette_mutation_fallback(self) -> None:
        """Test that settings.json with 'night_city' gives current_palette == 'night-city' and status text 'Dark · Night city'."""
        with tempfile.TemporaryDirectory() as tmp:
            places = Places(home=tmp, appdata=tmp, localappdata=tmp, temp=tmp, programdata=tmp)
            os.makedirs(places.app_dir, exist_ok=True)
            with open(places.settings_path, "w", encoding="utf-8") as f:
                json.dump({"theme": "dark", "palette": "night_city"}, f)

            app = create_test_app(places, mapped=True)
            try:
                app.update_idletasks()
                self.assertEqual(app.current_palette, "night-city")
                self.assertIn("Dark · Night city", app.status_theme_var.get())
            finally:
                app.destroy()

    def test_btn_copy_is_packed(self) -> None:
        """Assert btn_copy.winfo_manager() == 'pack'."""
        with fakes.tmpdir() as tmp:
            places = fakes.fake_places(tmp)
            app = create_test_app(places, mapped=True)
            try:
                app.update_idletasks()
                self.assertEqual(app.btn_copy.winfo_manager(), "pack")
            finally:
                app.destroy()
