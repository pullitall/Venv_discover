"""Main application window for Venv Discover."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from venvdiscover import classify, connections, packages, query, store, terminal, trashbin
from venvdiscover.actions import (
    activate_command,
    conda_exe,
    open_folder,
    run_conda_clean,
)
from venvdiscover.model import Env, Kind, LOCKED_KINDS, longpath, norm_key
from venvdiscover.places import Places
from venvdiscover.scanner import (
    EnvFound,
    EnvGone,
    EnvUpdated,
    Progress,
    ScanDone,
    Scanner,
    SizesDone,
)
from venvdiscover.sizes import folder_usage
from venvdiscover.terminal import Terminal, available_terminals, open_terminal
from venvdiscover.trashbin import (
    BinItem,
    bin_roots,
    cleanup_leftovers,
    list_bin,
    plan_delete,
    purge_expired,
)
from venvdiscover.ui.bin_tab import BinTab
from venvdiscover.ui.delete_dialog import (
    BinChanged,
    ConfirmDeleteDialog,
    DeleteFailed,
    DeleteWorker,
    Deleted,
    Measured,
)
from venvdiscover.ui.envs_table import EnvsTable
from venvdiscover.ui.format import fmt_shared_note, fmt_size
from venvdiscover.ui.packages_panel import PackagesPanel, SearchResults
from venvdiscover.ui.theme import (
    PALETTES,
    PALETTE_DISPLAY_NAMES,
    apply_theme,
    enable_dpi_awareness,
    get_magnifier_image,
    px,
    resolve_palette,
    resolve_theme,
    style_window,
)
from venvdiscover.walker import default_scan_drives, local_drives

logger = logging.getLogger(__name__)


@dataclass
class UIAction:
    fn: Callable[[], None]


@dataclass(frozen=True)
class DeleteFinished:
    pass


class DrivesDialog(tk.Toplevel):
    """Dialog allowing the user to select which drives to scan."""

    def __init__(self, parent: App) -> None:
        super().__init__(parent)
        self.parent_app = parent
        self.title("Scan Drives")
        self.transient(parent)
        self.resizable(False, False)

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Select drives to scan for virtual environments:").pack(anchor="w", pady=(0, 8))

        self.drives = local_drives()
        settings = store.load_settings(parent.places.settings_path)
        if settings.drives is not None:
            active_roots = {norm_key(d) for d in settings.drives}
        else:
            active_roots = {norm_key(d) for d in default_scan_drives(self.drives)}

        self.check_vars: list[tk.BooleanVar] = []
        for drive in self.drives:
            var = tk.BooleanVar(value=(norm_key(drive.root) in active_roots))
            self.check_vars.append(var)
            if drive.label:
                text = f"{drive.root} {drive.label} ({drive.fs})"
            else:
                text = f"{drive.root} ({drive.fs})"
            cb = ttk.Checkbutton(frame, text=text, variable=var)
            cb.pack(anchor="w", pady=2)

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill="x", pady=(12, 0))
        self.save_btn = ttk.Button(btn_frame, text="Save", command=self.on_save)
        self.save_btn.pack(side="right", padx=(4, 0))
        self.cancel_btn = ttk.Button(btn_frame, text="Cancel", command=self.destroy)
        self.cancel_btn.pack(side="right")

        self.bind("<Escape>", lambda e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        style_window(self)
        self.grab_set()

    def on_save(self) -> None:
        selected = [d.root for d, var in zip(self.drives, self.check_vars) if var.get()]
        settings = store.load_settings(self.parent_app.places.settings_path)
        settings.drives = selected
        store.save_settings(self.parent_app.places.settings_path, settings)
        self.destroy()
        self.parent_app.rescan()


class SettingsDialog(tk.Toplevel):
    """Themed Settings dialog with segmented theme controls, palette swatches, and primary Done button."""

    def __init__(self, parent: App) -> None:
        super().__init__(parent)
        self.parent = parent
        self.title("Settings")
        self.transient(parent)
        self.resizable(False, False)

        settings = store.load_settings(self.parent.places.settings_path)
        self._current_theme = settings.theme
        self._current_palette = resolve_palette(settings.palette)

        # StringVars kept for full backward compatibility with test suites
        self.theme_var = tk.StringVar(value=self._format_theme_display(self._current_theme))
        self.palette_var = tk.StringVar(value=self._format_palette_display(self._current_palette))

        self.theme_var.trace_add("write", lambda *a: self._on_theme_var_changed())
        self.palette_var.trace_add("write", lambda *a: self._on_palette_var_changed())

        pad_16 = px(self, 16)
        pad_8 = px(self, 8)
        frame = ttk.Frame(self, padding=(pad_16, pad_16))
        frame.pack(fill="both", expand=True)

        # 1. Theme Segmented Control
        ttk.Label(frame, text="Theme", font="VDHeading").pack(anchor="w", pady=(0, 4))
        theme_row = ttk.Frame(frame)
        theme_row.pack(fill="x", pady=(0, pad_16))

        self.theme_buttons: dict[str, ttk.Button] = {}
        themes = [("Dark", "dark"), ("Light", "light"), ("Match Windows", "match windows")]
        for label, val in themes:
            btn = ttk.Button(
                theme_row,
                text=label,
                command=lambda v=val: self._select_theme(v),
            )
            btn.pack(side="left", padx=(0, pad_8))
            self.theme_buttons[val] = btn

        # 2. Palette Swatches
        ttk.Label(frame, text="Palette", font="VDHeading").pack(anchor="w", pady=(0, 4))
        pal_grid = ttk.Frame(frame)
        pal_grid.pack(fill="x", pady=(0, pad_16))

        self.pal_frames: dict[str, tk.Frame] = {}
        self.pal_swatches: dict[str, tk.Frame] = {}
        self.pal_labels: dict[str, tk.Label] = {}
        palettes = [
            ("Default", "default", "#ffb224", "#1a1a17"),
            ("Night city", "night-city", "#00f0ff", "#0c101c"),
            ("Calm glass", "calm-glass", "#e3b341", "#161b22"),
            ("Bold editorial", "bold-editorial", "#d97706", "#1c1917"),
        ]

        for idx, (display, key, acc, pnl) in enumerate(palettes):
            row = idx // 2
            col = idx % 2
            card = tk.Frame(pal_grid, cursor="hand2", padx=px(self, 6), pady=px(self, 6), relief="flat", bd=0)
            card.grid(row=row, column=col, padx=px(self, 4), pady=px(self, 4), sticky="nsew")
            card.bind("<Button-1>", lambda e, k=key: self._select_palette(k))

            # Dot indicator with accent colour
            swatch_row = tk.Frame(card, bg=pnl)
            swatch_row.pack(fill="both", expand=True)
            swatch_row.bind("<Button-1>", lambda e, k=key: self._select_palette(k))

            dot = tk.Frame(swatch_row, bg=acc, width=px(self, 12), height=px(self, 12))
            dot.pack(side="left", padx=(px(self, 4), px(self, 8)))
            dot.pack_propagate(False)
            dot.bind("<Button-1>", lambda e, k=key: self._select_palette(k))

            lbl = tk.Label(swatch_row, text=display, bg=pnl, fg="#ece8dc", font=("Segoe UI", 9))
            lbl.pack(side="left")
            lbl.bind("<Button-1>", lambda e, k=key: self._select_palette(k))

            self.pal_frames[key] = card
            self.pal_swatches[key] = swatch_row
            self.pal_labels[key] = lbl

        pal_grid.grid_columnconfigure(0, weight=1)
        pal_grid.grid_columnconfigure(1, weight=1)

        # 3. Actions Row (Drives... and Primary Done button)
        action_row = ttk.Frame(frame)
        action_row.pack(fill="x", pady=(pad_8, 0))

        self.btn_drives = ttk.Button(action_row, text="Drives…", command=self._on_drives)
        self.btn_drives.pack(side="left")

        self.btn_caches = ttk.Button(action_row, text="Caches…", command=self._on_caches)
        self.btn_caches.pack(side="left", padx=(px(self, 8), 0))

        self.btn_done = ttk.Button(action_row, text="Done", style="Accent.TButton", command=self.destroy)
        self.btn_done.pack(side="right")

        self.bind("<Escape>", lambda e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self._refresh_ui_highlights()
        apply_theme(self, self._current_theme, self._current_palette)
        style_window(self)

    def _format_theme_display(self, t: str) -> str:
        t_norm = (t or "").strip().lower().replace("_", " ")
        if t_norm == "light":
            return "Light"
        if t_norm == "match windows":
            return "Match Windows"
        return "Dark"

    def _format_palette_display(self, p: str) -> str:
        p_norm = (p or "").strip().lower().replace("_", "-")
        return PALETTE_DISPLAY_NAMES.get(p_norm, "Default")

    def _select_theme(self, theme_key: str) -> None:
        self._current_theme = theme_key
        self.theme_var.set(self._format_theme_display(theme_key))
        self._save_and_apply()

    def _select_palette(self, pal_key: str) -> None:
        self._current_palette = pal_key
        self.palette_var.set(self._format_palette_display(pal_key))
        self._save_and_apply()

    def _on_theme_var_changed(self) -> None:
        sel = self.theme_var.get()
        theme_map = {"Dark": "dark", "Light": "light", "Match Windows": "match windows"}
        new_theme = theme_map.get(sel, "dark")
        if new_theme != self._current_theme:
            self._current_theme = new_theme
            self._save_and_apply()

    def _on_palette_var_changed(self) -> None:
        sel = self.palette_var.get()
        pal_map = {
            "Default": "default",
            "Night city": "night-city",
            "Calm glass": "calm-glass",
            "Bold editorial": "bold-editorial",
        }
        new_pal = pal_map.get(sel, "default")
        if new_pal != self._current_palette:
            self._current_palette = new_pal
            self._save_and_apply()

    _on_theme_changed = _on_theme_var_changed
    _on_palette_changed = _on_palette_var_changed

    def _refresh_ui_highlights(self) -> None:
        resolved_th = resolve_theme(self._current_theme)
        resolved_pal = resolve_palette(self._current_palette)
        cur_colors = PALETTES.get(resolved_pal, PALETTES["default"])[resolved_th]
        accent_col = cur_colors["amber"]
        line_col = cur_colors["line"]
        panel_col = cur_colors["panel"]
        text_col = cur_colors["text"]

        # Highlight active theme
        for val, btn in self.theme_buttons.items():
            if val == self._current_theme:
                btn.configure(style="Accent.TButton")
            else:
                btn.configure(style="TButton")

        # Highlight active palette card with dynamic palette accent and panel background
        for key, card in self.pal_frames.items():
            swatch = self.pal_swatches.get(key)
            lbl = self.pal_labels.get(key)
            if swatch:
                swatch.configure(bg=panel_col)
            if lbl:
                lbl.configure(bg=panel_col, fg=text_col)

            if key == resolved_pal:
                card.configure(bg=panel_col, highlightthickness=2, highlightbackground=accent_col, highlightcolor=accent_col)
            else:
                card.configure(bg=panel_col, highlightthickness=1, highlightbackground=line_col, highlightcolor=line_col)

    def _save_and_apply(self) -> None:
        s = store.load_settings(self.parent.places.settings_path)
        s.theme = self._current_theme
        s.palette = self._current_palette
        store.save_settings(self.parent.places.settings_path, s)

        self.parent.apply_theme_settings(self._current_theme, self._current_palette)
        apply_theme(self, self._current_theme, self._current_palette)
        style_window(self)
        self._refresh_ui_highlights()

    def _on_drives(self) -> None:
        DrivesDialog(self.parent)

    def _on_caches(self) -> None:
        from venvdiscover.ui.caches_dialog import CachesDialog
        CachesDialog(self.parent)


class CondaCleanDialog(tk.Toplevel):
    """Dialog displaying streamed output from 'conda clean --all -y'."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.title("Clean Conda Cache")
        self.geometry(f"{px(self, 600)}x{px(self, 350)}")
        if parent and getattr(parent, "winfo_viewable", lambda: False)():
            try:
                self.transient(parent)
            except Exception:
                pass

        frame = ttk.Frame(self, padding=10)
        frame.pack(fill="both", expand=True)

        self.text = tk.Text(frame, wrap="word", width=70, height=15)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set, state="disabled")

        self.text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        btn_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        btn_frame.pack(fill="x")

        self.close_btn = ttk.Button(btn_frame, text="Close", command=self.destroy, state="disabled")
        self.close_btn.pack(side="right")
        style_window(self)

    def append_line(self, line: str) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", line + "\n")
        self.text.see("end")
        self.text.configure(state="disabled")

    def finish(self) -> None:
        self.close_btn.configure(state="normal")
        self.close_btn.focus_set()


class App(tk.Tk):
    """Main Venv Discover application window."""

    def __init__(
        self,
        places: Places,
        *,
        scanner_factory: Callable[..., Scanner] = Scanner,
        drive_roots: list[str] | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.places = places
        self.scanner_factory = scanner_factory
        self.drive_roots = drive_roots
        self.environ = environ

        self.envs: dict[str, Env] = {}
        self.events: queue.Queue = queue.Queue()
        self._scanner: Scanner | None = None
        self._search_timer: str | None = None
        self._drain_timer: str | None = None
        self._last_scan_done_text: str = ""
        self._delete_in_progress: bool = False
        self._delete_worker_running: bool = False
        self._active_delete_dialog: ConfirmDeleteDialog | None = None
        self._startup_bin_done: bool = False
        self._deleted_keys: set[str] = set()
        self._status_restore_timer: str | None = None
        self._saved_status_text: str = ""
        self._sash_timer: str | None = None

        # Terminal detection and selection
        self.terminals = available_terminals(environ)
        settings = store.load_settings(self.places.settings_path)
        saved_term_id = settings.terminal
        term_map = {t.id: t for t in self.terminals}
        cmd_fallback = term_map.get("cmd") or (self.terminals[0] if self.terminals else Terminal("cmd", "Command Prompt", "cmd.exe"))
        self.current_terminal: Terminal = term_map.get(saved_term_id, cmd_fallback)

        self.title("Venv Discover")
        target_w = px(self, 1280)
        target_h = px(self, 760)
        try:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            if sw > 0 and sh > 0:
                max_w = max(px(self, 820), sw - px(self, 60))
                max_h = max(px(self, 450), sh - px(self, 80))
                win_w = min(target_w, max_w)
                win_h = min(target_h, max_h)
            else:
                win_w, win_h = target_w, target_h
        except Exception:
            win_w, win_h = target_w, target_h

        self.geometry(f"{win_w}x{win_h}")
        self.minsize(px(self, 820), px(self, 450))

        # Set window icon if available
        try:
            assets_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
            ico_path = os.path.join(assets_dir, "venvdiscover.ico")
            if os.path.exists(ico_path):
                self.iconbitmap(default=ico_path)
        except Exception:
            pass

        # Theme and color palette
        self.setting_theme_choice = settings.theme
        self.current_theme = resolve_theme(settings.theme)
        self.current_palette = resolve_palette(settings.palette)
        apply_theme(self, self.current_theme, self.current_palette)

        # 1. Status bar packed first at bottom so it can NEVER be squeezed out
        self._build_status_bar()

        # 2. Main Notebook with Environments and Bin tabs
        pad_12 = px(self, 12)
        pad_8 = px(self, 8)
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=pad_12, pady=(pad_8, 0))

        self.envs_frame = ttk.Frame(self.notebook)
        self.bin_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.envs_frame, text="Environments")
        self.notebook.add(self.bin_frame, text="Bin")

        # Bin tab widget
        self.bin_tab = BinTab(
            self.bin_frame,
            on_restore=self._on_bin_restore,
            on_forget=self._on_bin_forget,
        )
        self.bin_tab.pack(fill="both", expand=True)

        # 3. Actions bar packed first at bottom so paned never squeezes it out
        self._build_actions_bar(self.envs_frame)

        # 4. Top bar inside environments tab
        self._build_top_bar(self.envs_frame)

        # 5. PanedWindow with Cards
        self.paned = ttk.PanedWindow(self.envs_frame, orient="horizontal")
        self.paned.pack(fill="both", expand=True, pady=(0, pad_8))

        # Left Card: Environments Table
        self.left_card = ttk.Frame(self.paned, style="Card.TFrame", padding=(px(self, 10), px(self, 10)))
        self.paned.add(self.left_card, weight=3)

        self.envs_section_label = ttk.Label(self.left_card, text="ENVIRONMENTS · 0", style="Section.TLabel")
        self.envs_section_label.pack(anchor="w", pady=(0, px(self, 6)))

        self.envs_table = EnvsTable(
            self.left_card,
            on_select=self._on_table_select,
            on_double_click=self._on_table_double_click,
            on_context_menu=self._show_context_menu,
        )
        self.envs_table.pack(fill="both", expand=True)
        self.envs_table.bind("<Control-c>", lambda e: self._on_copy_path())
        self.envs_table.bind("<Control-C>", lambda e: self._on_copy_path())
        self.envs_table.tree.bind("<Delete>", lambda e: self._on_delete())
        self.envs_table.tree.bind("<Control-c>", lambda e: self._on_copy_path())
        self.envs_table.tree.bind("<Control-C>", lambda e: self._on_copy_path())

        # Right Card: Packages Panel and Search Results
        self.right_card = ttk.Frame(self.paned, style="Card.TFrame", padding=(px(self, 10), px(self, 10)))
        self.right_container = self.right_card
        self.paned.add(self.right_card, weight=2)

        self.packages_section_label = ttk.Label(self.right_card, text="PACKAGES", style="Section.TLabel")
        self.packages_section_label.pack(anchor="w", pady=(0, 2))

        self.packages_path_label = ttk.Label(self.right_card, text="", style="Muted.TLabel")
        self.packages_path_label.pack(anchor="w", pady=(0, px(self, 6)))

        self.packages_panel = PackagesPanel(self.right_card)
        self.search_results = SearchResults(self.right_card, on_select_env=self._on_search_select_env)
        self.packages_panel.pack(fill="both", expand=True)

        # Initial sash position (packages panel gets ~38% of width)
        self._sash_timer = self.after(50, self._adjust_initial_sash)

        # Context menu
        self._build_context_menu()

        # Load cached rows first
        cached = store.load_cache(self.places.cache_path)
        for env in cached:
            self.envs[env.key] = env
            self.envs_table.upsert(env)
        self._update_envs_count()

        self._update_action_states()
        self._refresh_bin_tab()
        self._start_bin_worker()

        # Start background queue polling (every 50 ms)
        self._drain_timer = self.after(50, self._drain_queue)



        # Ensure minsize fits the entire actions bar with margins after first idle
        self._minsize_id = self.after_idle(self._update_minsize)

        # Start initial scan
        self.rescan()

        # Clean shutdown on window close
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_top_bar(self, parent: ttk.Frame) -> None:
        pad_8 = px(self, 8)
        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(pad_8, pad_8))

        search_frame = ttk.Frame(bar)
        search_frame.pack(side="left", padx=(0, pad_8))

        self.mag_img = get_magnifier_image(self, size=14)
        lbl_mag = ttk.Label(search_frame, image=self.mag_img)
        lbl_mag.pack(side="left", padx=(px(self, 6), 0))

        self.search_var = tk.StringVar(value="Search packages…")
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=28)
        self.search_entry.pack(side="left")

        def _on_focus_in(e):
            if self.search_var.get() == "Search packages…":
                self.search_var.set("")

        def _on_focus_out(e):
            if not self.search_var.get().strip():
                self.search_var.set("Search packages…")

        self.search_entry.bind("<FocusIn>", _on_focus_in)
        self.search_entry.bind("<FocusOut>", _on_focus_out)
        self.search_var.trace_add("write", self._on_search_key)

        self.btn_rescan = ttk.Button(bar, text="⟳ Rescan", command=self.rescan)
        self.btn_rescan.pack(side="left", padx=(0, pad_8))

        self.btn_clean = ttk.Button(bar, text="Clean conda cache", command=self._on_clean_conda_cache, state="disabled")
        self.btn_clean.pack(side="left", padx=(0, pad_8))

        self.btn_caches = ttk.Button(bar, text="Caches…", command=self._on_open_caches)
        self.btn_caches.pack(side="left", padx=(0, pad_8))

        # Spacer
        ttk.Frame(bar).pack(side="left", fill="x", expand=True)

        self.btn_settings = ttk.Button(bar, text="⚙ Settings", command=self._on_open_settings)
        self.btn_settings.pack(side="right")

    def _update_minsize(self) -> None:
        """Update window minsize from action bar requested width plus margins."""
        try:
            if not self.winfo_exists():
                return
            if hasattr(self, "actions_bar") and self.actions_bar.winfo_exists():
                req_w = self.actions_bar.winfo_reqwidth()
                if req_w > 1:
                    min_w = max(px(self, 820), req_w + px(self, 24))
                    min_h = px(self, 450)
                    self.minsize(min_w, min_h)
        except Exception:
            pass

    def _build_actions_bar(self, parent: ttk.Frame) -> None:
        pad_8 = px(self, 8)
        bar = ttk.Frame(parent)
        bar.pack(side="bottom", fill="x", pady=(pad_8, pad_8))
        self.actions_bar = bar

        self.btn_open = ttk.Button(bar, text="Open folder", command=self._on_open_folder, state="disabled")
        self.btn_open.pack(side="left", padx=(0, pad_8))

        self.btn_copy = ttk.Button(
            bar,
            text="Copy activate command",
            command=self._on_copy_activate,
            state="disabled",
        )
        self.btn_copy.pack(side="left", padx=(0, pad_8))

        self.btn_copy_path = ttk.Button(
            bar,
            text="Copy path",
            command=self._on_copy_path,
            state="disabled",
        )
        self.btn_copy_path.pack(side="left", padx=(0, pad_8))

        # Split control: [Open terminal | <terminal> ▾]
        split_frame = ttk.Frame(bar)
        split_frame.pack(side="left", padx=(0, pad_8))

        self.btn_terminal = ttk.Button(
            split_frame,
            text="Open terminal",
            command=self._on_open_terminal,
            state="disabled",
            style="SplitLeft.Accent.TButton",
        )
        self.btn_terminal.pack(side="left")

        self.mb_terminal = ttk.Menubutton(
            split_frame,
            text=f"{self.current_terminal.label} \u25be",
            style="SplitRight.Accent.TMenubutton",
        )
        self.terminal_menu = tk.Menu(self.mb_terminal, tearoff=0)
        for term in self.terminals:
            self.terminal_menu.add_command(
                label=term.label,
                command=lambda t=term: self._on_select_terminal(t),
            )
        self.mb_terminal["menu"] = self.terminal_menu
        self.mb_terminal.pack(side="left")

        self.btn_delete = ttk.Button(
            bar,
            text="Delete…",
            command=self._on_delete,
            state="disabled",
            style="Danger.TButton",
        )
        self.btn_delete.pack(side="right")

        # Spacer pushing Delete to far right
        ttk.Frame(bar).pack(side="left", fill="x", expand=True)

    def _build_status_bar(self) -> None:
        pad_12 = px(self, 12)
        h_status = px(self, 28)

        status_frame = ttk.Frame(self, style="StatusBar.TFrame", height=h_status)
        status_frame.pack(side="bottom", fill="x")
        status_frame.pack_propagate(False)

        # 1px top border line
        self.status_top_line = tk.Frame(status_frame, height=1, bg="#2e2d29")
        self.status_top_line.pack(side="top", fill="x")

        status_inner = ttk.Frame(status_frame, style="StatusBar.TFrame", padding=(pad_12, px(self, 4)))
        status_inner.pack(fill="both", expand=True)

        self.status_dot_label = ttk.Label(status_inner, text="● ", style="StatusMuted.TLabel")
        self.status_dot_label.pack(side="left")

        self.status_var = tk.StringVar(value="")
        self.status_label = ttk.Label(status_inner, textvariable=self.status_var, style="StatusMuted.TLabel")
        self.status_label.pack(side="left")

        pal_name = PALETTES.get(self.current_palette, PALETTES["default"])["name"]
        th_label = "Match Windows" if getattr(self, "setting_theme_choice", "").strip().lower().replace("_", " ") == "match windows" else self.current_theme.capitalize()
        theme_text = f"{th_label} · {pal_name}"
        self.status_theme_var = tk.StringVar(value=theme_text)
        self.status_theme_label = ttk.Label(
            status_inner,
            textvariable=self.status_theme_var,
            style="StatusMuted.TLabel",
            cursor="hand2",
        )
        self.status_theme_label.pack(side="right")
        self.status_theme_label.bind("<Button-1>", lambda e: self._on_open_settings())

    def _build_context_menu(self) -> None:
        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(label="Open folder", command=self._on_open_folder)

        self.terminal_submenu = tk.Menu(self.context_menu, tearoff=0)
        for term in self.terminals:
            self.terminal_submenu.add_command(
                label=term.label,
                command=lambda t=term: self._on_pick_terminal_from_menu(t),
            )
        self.context_menu.add_cascade(label="Open terminal", menu=self.terminal_submenu)

        self.context_menu.add_separator()
        self.context_menu.add_command(label="Copy path", command=self._on_copy_path)
        self.context_menu.add_command(label="Copy Python path", command=self._on_copy_python_path)
        self.context_menu.add_command(label="Copy activate command", command=self._on_copy_activate)
        self.context_menu.add_command(label="Copy package list", command=self._on_copy_package_list)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Delete…", command=self._on_delete)

    def selected_envs(self) -> list[Env]:
        """Return list of currently selected Env objects."""
        keys = self.envs_table.selected_keys()
        return [self.envs[k] for k in keys if k in self.envs]

    def _log_action(self, action: str, env_path: str, detail: str) -> None:
        store.append_log(self.places.log_path, action, env_path, detail)

    def _current_drive_roots(self) -> list[str]:
        if self.drive_roots is not None:
            return self.drive_roots
        settings = store.load_settings(self.places.settings_path)
        if settings.drives is not None:
            return settings.drives
        return default_scan_drives(local_drives())

    def _refresh_bin_tab(self) -> None:
        roots = bin_roots(self.places, self._current_drive_roots())
        items = list_bin(roots)
        self.bin_tab.set_items(items, time.time())

    def _start_bin_worker(self) -> None:
        threading.Thread(target=self._run_startup_bin_worker, daemon=True).start()

    def _run_startup_bin_worker(self) -> None:
        try:
            roots = bin_roots(self.places, self._current_drive_roots())
            cleanup_leftovers(roots)
            items = list_bin(roots)
            purge_expired(items, time.time(), self._log_action)
            self.events.put(BinChanged())
        except Exception as ex:
            self.events.put(Progress(f"Startup bin cleanup failed: {ex}"))

    def _on_bin_restore(self, item: BinItem) -> None:
        self._deleted_keys.discard(norm_key(item.original_path))

        def worker() -> None:
            try:
                trashbin.restore(item, self.places, log=self._log_action)
                env = classify.probe(item.original_path, self.places)
                if env:
                    env = packages.fill(env)
                    if env.size is None:
                        env.size = item.size
                else:
                    env = Env(item.original_path, item.kind, item.name, size=item.size)
                self.events.put(EnvFound(env))
                self.events.put(BinChanged())
            except Exception as ex:
                err_msg = str(ex)
                self.events.put(UIAction(lambda m=err_msg: messagebox.showerror("Restore Failed", m)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_bin_forget(self, item: BinItem) -> None:
        msg = f"Delete {item.name} ({fmt_size(item.size)}) forever? This can't be undone."
        confirmed = messagebox.askyesno(
            "Delete forever",
            msg,
            default=messagebox.NO,
            parent=self,
        )
        if not confirmed:
            return

        def worker() -> None:
            try:
                trashbin.purge(item, self._log_action)
                self.events.put(BinChanged())
            except Exception as ex:
                err_msg = str(ex)
                self.events.put(UIAction(lambda m=err_msg: messagebox.showerror("Delete Failed", m)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_open_caches(self) -> None:
        from venvdiscover.ui.caches_dialog import CachesDialog
        CachesDialog(self)

    def _on_clean_conda_cache(self) -> None:
        bases = [e for e in self.envs.values() if e.kind == Kind.CONDA_BASE]
        if not bases:
            return

        confirmed = messagebox.askyesno(
            "Clean Conda Cache",
            "Clean the conda package cache? This will run 'conda clean --all -y'.",
            parent=self,
        )
        if not confirmed:
            return

        dialog = CondaCleanDialog(self)
        try:
            dialog.grab_set()
        except Exception:
            pass

        def worker() -> None:
            for base in bases:
                exe = conda_exe(base.path)
                if not exe:
                    self.events.put(UIAction(lambda p=base.path: dialog.append_line(f"No conda executable found for {p}")))
                    continue
                self._log_action("clean_conda", base.path, "started")
                self.events.put(UIAction(lambda p=base.path: dialog.append_line(f"Cleaning {p}…")))

                def on_line(line: str) -> None:
                    self.events.put(UIAction(lambda l=line: dialog.append_line(l)))

                exit_code = run_conda_clean(exe, on_line)
                self._log_action("clean_conda", base.path, f"exit code {exit_code}")
                self.events.put(UIAction(lambda c=exit_code: dialog.append_line(f"Finished (exit code {c})\n")))

            self.events.put(UIAction(dialog.finish))

        threading.Thread(target=worker, daemon=True).start()

    def request_delete(self, envs: list[Env]) -> None:
        """Measure unmeasured envs, plan deletion, show modal confirmation, and run worker."""
        if not envs or self._delete_in_progress or self._delete_worker_running:
            return

        self._delete_in_progress = True
        self._update_action_states()

        def _prepare() -> None:
            try:
                unmeasured = [e for e in envs if e.kind not in LOCKED_KINDS and e.size is None]
                if unmeasured:
                    self.events.put(UIAction(lambda: self.set_status("Measuring…")))
                    for e in unmeasured:
                        usage = folder_usage(e.path)
                        e.size, e.size_unique = usage.total, usage.unique
                        marker = trashbin.project_marker(e.path)
                        self.events.put(Measured(key=e.key, size=usage.total, project_marker=marker,
                                                 size_unique=usage.unique))

                to_bin, permanent, locked = trashbin.plan_delete(envs)

                k_dirs = connections.kernel_dirs(self.places)
                s_dirs = connections.shortcut_dirs(self.places)
                extras: dict[str, list[str]] = {}
                for e in to_bin + permanent:
                    k = connections.find_kernels(e.path, k_dirs)
                    s = connections.find_shortcuts(e.path, s_dirs)
                    ext = k + s
                    if ext:
                        extras[e.key] = ext

                self.events.put(UIAction(lambda: self._show_confirm_dialog(to_bin, permanent, locked, extras)))
            except Exception as ex:
                def _fail() -> None:
                    self._delete_in_progress = False
                    self._update_action_states()
                self.events.put(UIAction(_fail))
                self.events.put(Progress(f"Delete preparation failed: {ex}"))

        threading.Thread(target=_prepare, daemon=True).start()

    def _show_confirm_dialog(
        self,
        to_bin: list[Env],
        permanent: list[Env],
        locked: list[Env],
        extras: dict[str, list[str]],
    ) -> None:
        if self._last_scan_done_text:
            self.set_status(self._last_scan_done_text)

        def on_confirm() -> None:
            self._delete_worker_running = True
            self._delete_in_progress = True
            self.btn_delete.configure(state="disabled")
            worker = DeleteWorker(
                "delete",
                dialog.deletable_envs,
                self.places,
                self.events,
                log=self._log_action,
                permanent_keys={e.key for e in dialog.permanent},
            )
            worker.start()

            def _watch() -> None:
                worker.join()
                self.events.put(DeleteFinished())

            threading.Thread(target=_watch, daemon=True).start()

        dialog = ConfirmDeleteDialog(
            self,
            to_bin=to_bin,
            permanent=permanent,
            locked=locked,
            extras=extras,
            on_confirm=on_confirm,
        )
        self._active_delete_dialog = dialog
        try:
            dialog.grab_set()
        except Exception:
            pass

        def on_dialog_destroy(event: tk.Event) -> None:
            if event.widget == dialog:
                self._active_delete_dialog = None
                if not self._delete_worker_running:
                    self._delete_in_progress = False
                    self._update_action_states()

        dialog.bind("<Destroy>", on_dialog_destroy)

    def handle_event(self, ev: object) -> bool:
        """Handle an event from the scanner queue. Return True if handled, False otherwise."""
        if isinstance(ev, UIAction):
            try:
                ev.fn()
            except Exception as e:
                logger.debug("Error executing UIAction: %s", e)
            return True

        if isinstance(ev, DeleteFinished):
            self._delete_worker_running = False
            self._delete_in_progress = False
            self._update_action_states()
            return True

        if isinstance(ev, Measured):
            if ev.key in self.envs:
                self.envs[ev.key].size = ev.size
                if ev.size_unique is not None:
                    self.envs[ev.key].size_unique = ev.size_unique
                self.envs_table.upsert(self.envs[ev.key])
                self._refresh_env_header(self.envs[ev.key])
                if self.search_var.get().strip() and self.search_var.get() != "Search packages…":
                    self._on_search_key()
            return True

        if isinstance(ev, Deleted):
            self._deleted_keys.add(ev.key)
            self.envs.pop(ev.key, None)
            self.envs_table.remove(ev.key)
            self._update_envs_count()
            selected = self.selected_envs()
            if len(selected) == 1:
                self.packages_panel.show(selected[0])
            else:
                self.packages_panel.show(None)
            self._update_action_states()
            if self.search_var.get().strip() and self.search_var.get() != "Search packages…":
                self._on_search_key()
            return True

        if isinstance(ev, DeleteFailed):
            messagebox.showerror("Delete Failed", ev.message)
            return True

        if isinstance(ev, BinChanged):
            self._startup_bin_done = True
            self._refresh_bin_tab()
            return True

        if isinstance(ev, EnvFound):
            if ev.env.key in self._deleted_keys:
                if not os.path.isdir(longpath(ev.env.path)):
                    return True
                self._deleted_keys.discard(ev.env.key)
            self.envs[ev.env.key] = ev.env
            self.envs_table.upsert(ev.env)
            self._update_envs_count()
            self._update_action_states()
            if self.search_var.get().strip() and self.search_var.get() != "Search packages…":
                self._on_search_key()
            return True

        if isinstance(ev, EnvUpdated):
            if ev.env.key in self._deleted_keys:
                if not os.path.isdir(longpath(ev.env.path)):
                    return True
                self._deleted_keys.discard(ev.env.key)
            self.envs[ev.env.key] = ev.env
            self.envs_table.upsert(ev.env)
            selected = self.selected_envs()
            if len(selected) == 1 and selected[0].key == ev.env.key:
                self.packages_panel.show(ev.env)
                self._refresh_env_header(ev.env)
            self._update_action_states()
            if self.search_var.get().strip() and self.search_var.get() != "Search packages…":
                self._on_search_key()
            return True

        if isinstance(ev, EnvGone):
            self.envs.pop(ev.key, None)
            self.envs_table.remove(ev.key)
            self._update_envs_count()
            self._update_action_states()
            if self.search_var.get().strip() and self.search_var.get() != "Search packages…":
                self._on_search_key()
            return True

        if isinstance(ev, Progress):
            self.set_status(ev.text)
            return True

        if isinstance(ev, ScanDone):
            if isinstance(ev.seconds, float):
                sec_str = f"{int(ev.seconds)}" if ev.seconds.is_integer() else f"{ev.seconds:.1f}"
            else:
                sec_str = f"{ev.seconds}"
            self._last_scan_done_text = f"Found {ev.total} envs in {sec_str} s"
            self.set_status(self._last_scan_done_text)
            store.save_cache(self.places.cache_path, list(self.envs.values()))
            return True

        if isinstance(ev, SizesDone):
            if self._last_scan_done_text:
                self.set_status(self._last_scan_done_text)
            store.save_cache(self.places.cache_path, list(self.envs.values()))
            return True

        return False

    def set_status(self, text: str) -> None:
        """Update status line text."""
        self.status_var.set(text)

    def rescan(self) -> None:
        """Stop current scan and restart background discovery with latest drive settings."""
        if self._scanner:
            self._scanner.stop()
            self._scanner.join(timeout=1.0)
            self._scanner = None

        roots = self._current_drive_roots()
        settings = store.load_settings(self.places.settings_path)
        self._scanner = self.scanner_factory(
            places=self.places,
            settings=settings,
            cached=list(self.envs.values()),
            out=self.events,
            roots=roots,
        )
        self._scanner.start()

    def _drain_queue(self) -> None:
        try:
            count = 0
            while count < 500:
                try:
                    ev = self.events.get_nowait()
                except queue.Empty:
                    break
                try:
                    self.handle_event(ev)
                except Exception as e:
                    logger.debug("Error handling event: %s", e)
                count += 1
        finally:
            self._drain_timer = self.after(50, self._drain_queue)

    def _on_search_key(self, *args) -> None:
        if self._search_timer:
            self.after_cancel(self._search_timer)
        self._search_timer = self.after(150, self._perform_search)

    def _perform_search(self) -> None:
        self._search_timer = None
        raw_q = self.search_var.get().strip()
        q = "" if raw_q == "Search packages…" else raw_q
        if not q:
            self.search_results.pack_forget()
            self.packages_panel.pack(fill="both", expand=True)
            envs = self.selected_envs()
            self.packages_panel.show(envs[0] if len(envs) == 1 else None)
        else:
            self.packages_panel.pack_forget()
            self.search_results.pack(fill="both", expand=True)
            results = query.search(self.envs.values(), q)
            self.search_results.show(results[:1000], len(results))

    @staticmethod
    def _env_header_text(env: Env) -> str:
        """The path under the packages heading, plus what deleting frees when other folders share its files."""
        note = fmt_shared_note(env.size, env.size_unique)
        return f"{env.path}\n{note}" if note else env.path

    def _refresh_env_header(self, env: Env) -> None:
        """Update the header after a size change, if this env is the one selected."""
        selected = self.selected_envs()
        if len(selected) == 1 and selected[0].key == env.key and hasattr(self, "packages_path_label"):
            self.packages_path_label.config(text=self._env_header_text(env))

    def _on_table_select(self, keys: list[str]) -> None:
        envs = self.selected_envs()
        raw_q = self.search_var.get().strip()
        q = "" if raw_q == "Search packages…" else raw_q
        if not q:
            if len(envs) == 1:
                if hasattr(self, "packages_section_label"):
                    self.packages_section_label.config(text=f"PACKAGES · {envs[0].name}")
                if hasattr(self, "packages_path_label"):
                    self.packages_path_label.config(text=self._env_header_text(envs[0]))
                self.packages_panel.show(envs[0])
            else:
                if hasattr(self, "packages_section_label"):
                    self.packages_section_label.config(text="PACKAGES")
                if hasattr(self, "packages_path_label"):
                    self.packages_path_label.config(text="")
                self.packages_panel.show(None)
        self._update_action_states()

    def _on_search_select_env(self, env_key: str) -> None:
        if self.envs_table.tree.exists(env_key):
            self.envs_table.tree.selection_set(env_key)
            self.envs_table.tree.see(env_key)
            self._update_action_states()

    def _on_table_double_click(self, key: str) -> None:
        if key in self.envs:
            open_folder(self.envs[key].path)

    def _show_context_menu(self, event: tk.Event) -> None:
        self._update_action_states()
        envs = self.selected_envs()
        self.context_menu.entryconfig("Open folder", state="normal" if envs else "disabled")
        self.context_menu.entryconfig("Open terminal", state="normal" if len(envs) == 1 else "disabled")
        self.context_menu.entryconfig("Copy path", state="normal" if envs else "disabled")
        can_copy_py = len(envs) == 1 and bool(self._python_exe_for_env(envs[0]))
        self.context_menu.entryconfig("Copy Python path", state="normal" if can_copy_py else "disabled")
        can_copy_act = len(envs) == 1 and bool(activate_command(envs[0]))
        self.context_menu.entryconfig("Copy activate command", state="normal" if can_copy_act else "disabled")
        can_copy_pkgs = len(envs) == 1 and bool(self._packages_to_copy(envs[0]))
        self.context_menu.entryconfig("Copy package list", state="normal" if can_copy_pkgs else "disabled")
        if self._delete_in_progress or self._delete_worker_running:
            self.context_menu.entryconfig("Delete…", state="disabled")
        else:
            all_locked = bool(envs) and all(e.kind in LOCKED_KINDS for e in envs)
            self.context_menu.entryconfig("Delete…", state="disabled" if (not envs or all_locked) else "normal")
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    def _update_action_states(self) -> None:
        has_conda_base = any(e.kind == Kind.CONDA_BASE for e in self.envs.values())
        self.btn_clean.configure(state="normal" if has_conda_base else "disabled")

        envs = self.selected_envs()
        if not envs:
            self.btn_open.config(state="disabled")
            self.btn_terminal.config(state="disabled")
            self.btn_copy_path.config(state="disabled")
            self.btn_copy.config(state="disabled")
            self.btn_delete.config(state="disabled")
            return

        self.btn_open.config(state="normal")
        self.btn_terminal.config(state="normal" if len(envs) == 1 else "disabled")
        self.btn_copy_path.config(state="normal")

        if len(envs) == 1 and activate_command(envs[0]):
            self.btn_copy.config(state="normal")
        else:
            self.btn_copy.config(state="disabled")

        if self._delete_in_progress or self._delete_worker_running:
            self.btn_delete.config(state="disabled")
        else:
            all_locked = all(e.kind in LOCKED_KINDS for e in envs)
            if all_locked:
                self.btn_delete.config(state="disabled")
            else:
                self.btn_delete.config(state="normal")

    def _on_open_folder(self) -> None:
        envs = self.selected_envs()
        if envs:
            open_folder(envs[0].path)

    def _on_select_terminal(self, term: Terminal) -> None:
        self.current_terminal = term
        self.mb_terminal.configure(text=f"{term.label} \u25be")
        settings = store.load_settings(self.places.settings_path)
        settings.terminal = term.id
        store.save_settings(self.places.settings_path, settings)

    def _on_pick_terminal_from_menu(self, term: Terminal) -> None:
        self._on_select_terminal(term)
        self._on_open_terminal()

    def selected_envs_in_table_order(self) -> list[Env]:
        """Return selected Env objects sorted in the exact order they appear in the table."""
        selected_set = set(self.envs_table.selected_keys())
        return [self.envs[k] for k in self.envs_table.tree.get_children("") if k in selected_set and k in self.envs]

    def _python_exe_for_env(self, env: Env) -> str | None:
        if env.kind in (Kind.CONDA_BASE, Kind.CONDA, Kind.PYTHON):
            exe = os.path.join(env.path, "python.exe")
        else:
            exe = os.path.join(env.path, "Scripts", "python.exe")
        if os.path.isfile(longpath(exe)):
            return exe
        return None

    def _packages_to_copy(self, env: Env) -> list[tuple[str, str]]:
        return sorted([(p.name, p.version) for p in env.packages], key=lambda x: x[0].lower())

    def _set_temporary_status(self, msg: str, timeout_ms: int = 3000) -> None:
        if self._status_restore_timer is not None:
            try:
                self.after_cancel(self._status_restore_timer)
            except Exception:
                pass
            self._status_restore_timer = None
        else:
            self._saved_status_text = self.status_var.get()

        self.status_var.set(msg)

        def _restore() -> None:
            self._status_restore_timer = None
            try:
                self.status_var.set(self._saved_status_text)
            except Exception:
                pass

        self._status_restore_timer = self.after(timeout_ms, _restore)

    def _on_copy_path(self) -> None:
        envs = self.selected_envs_in_table_order()
        if not envs:
            return
        paths = "\n".join(e.path for e in envs)
        self.clipboard_clear()
        self.clipboard_append(paths)
        if len(envs) == 1:
            self._set_temporary_status(f"Copied path of {envs[0].name}")
        else:
            self._set_temporary_status(f"Copied {len(envs)} paths")

    def _on_copy_python_path(self) -> None:
        envs = self.selected_envs()
        if len(envs) == 1:
            exe = self._python_exe_for_env(envs[0])
            if exe:
                self.clipboard_clear()
                self.clipboard_append(exe)
                self._set_temporary_status(f"Copied Python path of {envs[0].name}")

    def _on_copy_package_list(self) -> None:
        envs = self.selected_envs()
        if len(envs) == 1:
            pkgs = self._packages_to_copy(envs[0])
            if pkgs:
                lines = "\n".join(f"{name}=={ver}" if ver else name for name, ver in pkgs)
                self.clipboard_clear()
                self.clipboard_append(lines)
                count = len(pkgs)
                pkg_str = "1 package" if count == 1 else f"{count} packages"
                self._set_temporary_status(f"Copied {pkg_str} of {envs[0].name}")

    def _on_copy_activate(self) -> None:
        envs = self.selected_envs()
        if len(envs) == 1:
            cmd = activate_command(envs[0])
            if cmd:
                self.clipboard_clear()
                self.clipboard_append(cmd)
                self._set_temporary_status(f"Copied activate command of {envs[0].name}")

    def _on_open_terminal(self) -> None:
        envs = self.selected_envs()
        if len(envs) == 1:
            try:
                open_terminal(envs[0], self.current_terminal, self.places)
            except FileNotFoundError:
                messagebox.showerror(
                    "Error",
                    f"{self.current_terminal.label} wasn't found. Pick another terminal.",
                    parent=self,
                )
            except OSError as exc:
                messagebox.showerror(
                    "Error",
                    f"{self.current_terminal.label} couldn't be started: {exc}",
                    parent=self,
                )

    def _on_delete(self) -> None:
        if self._delete_in_progress or self._delete_worker_running:
            return
        envs = self.selected_envs()
        if envs:
            self.request_delete(envs)

    def _on_open_settings(self) -> None:
        SettingsDialog(self)

    def apply_theme_settings(self, theme: str, palette: str) -> None:
        self.setting_theme_choice = theme
        self.current_theme = resolve_theme(theme)
        self.current_palette = resolve_palette(palette)
        apply_theme(self, self.current_theme, self.current_palette)

        pal_name = PALETTES.get(self.current_palette, PALETTES["default"])["name"]
        th_label = "Match Windows" if theme.strip().lower().replace("_", " ") == "match windows" else self.current_theme.capitalize()
        theme_text = f"{th_label} · {pal_name}"
        if hasattr(self, "status_theme_var"):
            self.status_theme_var.set(theme_text)
        if hasattr(self, "status_top_line"):
            col = PALETTES.get(self.current_palette, PALETTES["default"])[self.current_theme]
            self.status_top_line.configure(bg=col["line"])

    def _update_envs_count(self) -> None:
        if hasattr(self, "envs_section_label"):
            self.envs_section_label.config(text=f"ENVIRONMENTS · {len(self.envs)}")

    def _on_open_drives(self) -> None:
        DrivesDialog(self)

    def _adjust_initial_sash(self) -> None:
        self._sash_timer = None
        try:
            if not getattr(self, "paned", None) or not self.winfo_exists():
                return
            w = self.paned.winfo_width()
            if w > 20:
                self.paned.sashpos(0, int(round(w * 0.67)))
        except Exception:
            pass

    def _on_close(self) -> None:
        if self._scanner:
            self._scanner.stop()
            self._scanner = None
        store.save_cache(self.places.cache_path, list(self.envs.values()))
        self.destroy()

    def destroy(self) -> None:
        if hasattr(self, "_minsize_id") and self._minsize_id:
            try:
                self.after_cancel(self._minsize_id)
            except Exception:
                pass
            self._minsize_id = None

        if self._active_delete_dialog:
            try:
                if self._active_delete_dialog.winfo_exists():
                    self._active_delete_dialog.destroy()
            except Exception:
                pass
            self._active_delete_dialog = None

        if self._sash_timer:
            try:
                self.after_cancel(self._sash_timer)
            except Exception:
                pass
            self._sash_timer = None

        if self._drain_timer:
            try:
                self.after_cancel(self._drain_timer)
            except Exception:
                pass
            self._drain_timer = None

        if self._search_timer:
            try:
                self.after_cancel(self._search_timer)
            except Exception:
                pass
            self._search_timer = None

        if self._status_restore_timer:
            try:
                self.after_cancel(self._status_restore_timer)
            except Exception:
                pass
            self._status_restore_timer = None

        if self._scanner:
            self._scanner.stop()
            self._scanner = None

        super().destroy()


def main() -> None:
    """Entry point for running Venv Discover."""
    enable_dpi_awareness()
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("VenvDiscover.App")
    except Exception:
        pass
    places = Places.from_environ()
    app = App(places)
    app.mainloop()
