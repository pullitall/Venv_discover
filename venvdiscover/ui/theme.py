"""Themes: colour palettes, named fonts, rounded ttk elements and the title bar colours."""

from __future__ import annotations

import math
import os
import struct
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
import zlib

from venvdiscover.model import Kind

# ---------------------------------------------------------------------------
# Exact color palettes (dark and light)
# Variables: --bg, --panel, --line, --text, --muted, --amber, --amber-ink, --amber-soft, --danger
# Selection color is solid blend of amber alpha (0.12) over panel.
# ---------------------------------------------------------------------------

_RAW_PALETTES: dict[str, dict[str, dict[str, str]]] = {
    "default": {
        "dark": {
            "bg": "#11110f",
            "panel": "#1a1a17",
            "line": "#2e2d29",
            "text": "#ece8dc",
            "muted": "#8f8a7e",
            "amber": "#ffb224",
            "amber_ink": "#241800",
            "amber_soft": "#352d1b",
            "selection": "#352d1b",
            "stripe_even": "#1a1a17",
            "stripe_odd": "#141412",
            "danger": "#ff6a5a",
            "danger_ink": "#ffffff",
        },
        "light": {
            "bg": "#f4f1e8",
            "panel": "#fbfaf5",
            "line": "#ddd8c8",
            "text": "#1d1c18",
            "muted": "#676358",
            "amber": "#985d00",
            "amber_ink": "#fff8e8",
            "amber_soft": "#f9ebd7",
            "selection": "#f9ebd7",
            "stripe_even": "#fbfaf5",
            "stripe_odd": "#f2eee3",
            "danger": "#c13429",
            "danger_ink": "#ffffff",
        },
    },
    "night-city": {
        "dark": {
            "bg": "#060810",
            "panel": "#0c101c",
            "line": "#1e263d",
            "text": "#e2e8f0",
            "muted": "#8091a7",
            "amber": "#00f0ff",
            "amber_ink": "#041017",
            "amber_soft": "#0b2b37",
            "selection": "#0b2b37",
            "stripe_even": "#0c101c",
            "stripe_odd": "#080c16",
            "danger": "#f43f5e",
            "danger_ink": "#ffffff",
        },
        "light": {
            "bg": "#f0f4f8",
            "panel": "#ffffff",
            "line": "#cbd5e1",
            "text": "#0f172a",
            "muted": "#475569",
            "amber": "#026aa2",
            "amber_ink": "#ffffff",
            "amber_soft": "#e6f0f6",
            "selection": "#e6f0f6",
            "stripe_even": "#ffffff",
            "stripe_odd": "#f1f5f9",
            "danger": "#e11d48",
            "danger_ink": "#ffffff",
        },
    },
    "calm-glass": {
        "dark": {
            "bg": "#0d1117",
            "panel": "#161b22",
            "line": "#30363d",
            "text": "#f0f6fc",
            "muted": "#9ba5b1",
            "amber": "#e3b341",
            "amber_ink": "#161b22",
            "amber_soft": "#333026",
            "selection": "#333026",
            "stripe_even": "#161b22",
            "stripe_odd": "#10141a",
            "danger": "#f85149",
            "danger_ink": "#ffffff",
        },
        "light": {
            "bg": "#f6f8fa",
            "panel": "#ffffff",
            "line": "#d0d7de",
            "text": "#1f2328",
            "muted": "#4d5560",
            "amber": "#8c5d00",
            "amber_ink": "#ffffff",
            "amber_soft": "#f2ebd7",
            "selection": "#f2ebd7",
            "stripe_even": "#ffffff",
            "stripe_odd": "#f3f4f6",
            "danger": "#cf222e",
            "danger_ink": "#ffffff",
        },
    },
    "bold-editorial": {
        "dark": {
            "bg": "#0a0a0c",
            "panel": "#141418",
            "line": "#2c2c36",
            "text": "#fbfbfe",
            "muted": "#9a9aaf",
            "amber": "#f59e0b",
            "amber_ink": "#141418",
            "amber_soft": "#342816",
            "selection": "#342816",
            "stripe_even": "#141418",
            "stripe_odd": "#0e0e12",
            "danger": "#ef4444",
            "danger_ink": "#ffffff",
        },
        "light": {
            "bg": "#f5f5f7",
            "panel": "#ffffff",
            "line": "#d0d0dc",
            "text": "#0a0a0e",
            "muted": "#4a4a58",
            "amber": "#9a4505",
            "amber_ink": "#ffffff",
            "amber_soft": "#f5eae5",
            "selection": "#f5eae5",
            "stripe_even": "#ffffff",
            "stripe_odd": "#f0f0f4",
            "danger": "#dc2626",
            "danger_ink": "#ffffff",
        },
    },
}

PALETTES: dict[str, dict[str, dict[str, str]]] = {}
PALETTE_DISPLAY_NAMES = {
    "default": "Default",
    "night-city": "Night city",
    "calm-glass": "Calm glass",
    "bold-editorial": "Bold editorial",
}

for k, v in _RAW_PALETTES.items():
    dark_dict = dict(v["dark"])
    dark_dict["amber-ink"] = dark_dict["amber_ink"]
    dark_dict["amber-soft"] = dark_dict["amber_soft"]
    light_dict = dict(v["light"])
    light_dict["amber-ink"] = light_dict["amber_ink"]
    light_dict["amber-soft"] = light_dict["amber_soft"]

    pal_entry = {
        "name": PALETTE_DISPLAY_NAMES[k],
        "dark": dark_dict,
        "light": light_dict,
    }
    PALETTES[k] = pal_entry
    PALETTES[PALETTE_DISPLAY_NAMES[k]] = pal_entry

# Kind colors for tree markers
KIND_COLORS: dict[Kind | str, str] = {
    Kind.VENV: "#22c55e",
    Kind.CONDA: "#06b6d4",
    Kind.CONDA_BASE: "#0284c7",
    Kind.APP_VENV: "#a855f7",
    Kind.PYTHON: "#f59e0b",
    Kind.TEMP_VENV: "#6b7280",
}


def hex_to_rgba(hex_str: str, alpha: int = 255) -> tuple[int, int, int, int]:
    """Parse hex color (#RRGGBB) to (R, G, B, A)."""
    h = hex_str.lstrip("#")
    if len(h) == 6:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha
    return 0, 0, 0, alpha


def blend_hex(hex1: str, hex2: str, ratio: float) -> str:
    """Blend hex1 into hex2 by ratio (0.0 = hex1, 1.0 = hex2)."""
    r1, g1, b1, _ = hex_to_rgba(hex1)
    r2, g2, b2, _ = hex_to_rgba(hex2)
    r = int(round(r1 * (1.0 - ratio) + r2 * ratio))
    g = int(round(g1 * (1.0 - ratio) + g2 * ratio))
    b = int(round(b1 * (1.0 - ratio) + b2 * ratio))
    return f"#{max(0, min(255, r)):02x}{max(0, min(255, g)):02x}{max(0, min(255, b)):02x}"


def get_extended_colors(colors: dict[str, str], is_dark: bool) -> dict[str, str]:
    """Compute state steps (hover, pressed, borders) for the palette."""
    ext = dict(colors)
    text_color = colors["text"]
    panel_color = colors["panel"]
    line_color = colors["line"]
    amber_color = colors["amber"]
    danger_color = colors["danger"]

    if is_dark:
        ext["panel_hover"] = blend_hex(panel_color, text_color, 0.05)
        ext["panel_pressed"] = blend_hex(panel_color, text_color, 0.10)
        ext["line_hover"] = blend_hex(line_color, text_color, 0.15)
        ext["amber_hover"] = blend_hex(amber_color, "#ffffff", 0.18)
        ext["amber_pressed"] = blend_hex(amber_color, "#000000", 0.18)
        ext["danger_hover"] = danger_color
        ext["danger_pressed"] = blend_hex(danger_color, "#000000", 0.20)
    else:
        ext["panel_hover"] = blend_hex(panel_color, text_color, 0.04)
        ext["panel_pressed"] = blend_hex(panel_color, text_color, 0.08)
        ext["line_hover"] = blend_hex(line_color, text_color, 0.12)
        ext["amber_hover"] = blend_hex(amber_color, "#ffffff", 0.15)
        ext["amber_pressed"] = blend_hex(amber_color, "#000000", 0.15)
        ext["danger_hover"] = danger_color
        ext["danger_pressed"] = blend_hex(danger_color, "#000000", 0.20)

    return ext


# ---------------------------------------------------------------------------
# DPI helper: px()
# ---------------------------------------------------------------------------

def px(widget: tk.Widget | None, n: int | float) -> int:
    """Scale a logical pixel size by the widget's DPI relative to 96 DPI."""
    if widget is None or n == 0:
        return int(round(n))
    try:
        fpixels = widget.winfo_fpixels("1i")
        return int(round(n * fpixels / 96.0))
    except Exception:
        return int(round(n))


# ---------------------------------------------------------------------------
# In-Memory PNG Encoder (pure standard library zlib + struct)
# ---------------------------------------------------------------------------

def encode_png_rgba(width: int, height: int, rgba_bytes: bytes) -> bytes:
    """Encode 32-bit RGBA pixels into standard PNG bytes."""
    raw_lines = bytearray()
    row_bytes = width * 4
    for y in range(height):
        raw_lines.append(0)  # filter 0 (None)
        raw_lines.extend(rgba_bytes[y * row_bytes : (y + 1) * row_bytes])
    compressed = zlib.compress(bytes(raw_lines), level=6)
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    ihdr_crc = struct.pack(">I", zlib.crc32(b"IHDR" + ihdr_data))
    idat_crc = struct.pack(">I", zlib.crc32(b"IDAT" + compressed))
    iend_crc = struct.pack(">I", zlib.crc32(b"IEND"))
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I4s", 13, b"IHDR") + ihdr_data + ihdr_crc
        + struct.pack(">I4s", len(compressed), b"IDAT") + compressed + idat_crc
        + struct.pack(">I4s", 0, b"IEND") + iend_crc
    )


_BOX_CACHE: dict[tuple, bytes] = {}


def render_rounded_box_png(
    width: int,
    height: int,
    radius: float,
    border_width: float,
    fill_rgba: tuple[int, int, int, int],
    border_rgba: tuple[int, int, int, int],
    bg_rgba: tuple[int, int, int, int] | None = None,
    corners: tuple[bool, bool, bool, bool] = (True, True, True, True),
    left_divider_rgba: tuple[int, int, int, int] | None = None,
) -> bytes:
    """Render anti-aliased rounded rectangle PNG using analytical SDF with 2x supersampling and symmetry."""
    bg = bg_rgba if bg_rgba is not None else (0, 0, 0, 0)
    cache_key = (
        width,
        height,
        float(radius),
        float(border_width),
        fill_rgba,
        border_rgba,
        bg,
        corners,
        left_divider_rgba,
    )
    if cache_key in _BOX_CACHE:
        return _BOX_CACHE[cache_key]

    tl, tr, br, bl = corners
    fill_a = fill_rgba[3] if len(fill_rgba) > 3 else 255
    border_a = border_rgba[3] if len(border_rgba) > 3 else 255
    bg_a = bg[3] if len(bg) > 3 else 255

    half_w = width / 2.0
    half_h = height / 2.0

    fr, fg, fb = fill_rgba[0], fill_rgba[1], fill_rgba[2]
    br_r, br_g, br_b = border_rgba[0], border_rgba[1], border_rgba[2]
    bgr, bgg, bgb = bg[0], bg[1], bg[2]

    # Check symmetry: divider breaks left/right symmetry
    is_quad_symmetric = (tl == tr == br == bl) and (left_divider_rgba is None)
    is_vert_symmetric = (tl == bl and tr == br)

    half_w_int = (width + 1) // 2
    half_h_int = (height + 1) // 2

    calc_h = half_h_int if (is_quad_symmetric or is_vert_symmetric) else height
    calc_w = half_w_int if is_quad_symmetric else width

    offsets = ((0.25, 0.25), (0.25, 0.75), (0.75, 0.25), (0.75, 0.75))
    pixels: dict[tuple[int, int], tuple[int, int, int, int]] = {}

    for y in range(calc_h):
        for x in range(calc_w):
            total_r = total_g = total_b = total_a = 0
            for ox, oy in offsets:
                px_val = x + ox
                py = y + oy

                if px_val < half_w:
                    rc = radius if (tl if py < half_h else bl) else 0.0
                else:
                    rc = radius if (tr if py < half_h else br) else 0.0

                bx = half_w - rc
                by = half_h - rc
                qx = abs(px_val - half_w) - bx
                qy = abs(py - half_h) - by
                dx = qx if qx > 0.0 else 0.0
                dy = qy if qy > 0.0 else 0.0
                m = qx if qx > qy else qy
                d = math.sqrt(dx * dx + dy * dy) + (m if m < 0.0 else 0.0) - rc

                if d > 0.0:
                    total_r += bgr
                    total_g += bgg
                    total_b += bgb
                    total_a += bg_a
                elif d > -border_width:
                    total_r += br_r
                    total_g += br_g
                    total_b += br_b
                    total_a += border_a
                else:
                    total_r += fr
                    total_g += fg
                    total_b += fb
                    total_a += fill_a

            pixels[(x, y)] = (
                (total_r + 2) >> 2,
                (total_g + 2) >> 2,
                (total_b + 2) >> 2,
                (total_a + 2) >> 2,
            )

    rgba = bytearray(width * height * 4)
    for y in range(height):
        src_y = y
        if (is_quad_symmetric or is_vert_symmetric) and y >= half_h_int:
            src_y = height - 1 - y
        row_offset = y * width * 4
        for x in range(width):
            src_x = x
            if is_quad_symmetric and x >= half_w_int:
                src_x = width - 1 - x
            pix = pixels[(src_x, src_y)]
            idx = row_offset + x * 4
            if x == 0 and left_divider_rgba is not None:
                rgba[idx] = left_divider_rgba[0]
                rgba[idx + 1] = left_divider_rgba[1]
                rgba[idx + 2] = left_divider_rgba[2]
                rgba[idx + 3] = left_divider_rgba[3]
            else:
                rgba[idx] = pix[0]
                rgba[idx + 1] = pix[1]
                rgba[idx + 2] = pix[2]
                rgba[idx + 3] = pix[3]

    result = encode_png_rgba(width, height, bytes(rgba))
    _BOX_CACHE[cache_key] = result
    return result


def render_underline_tab_png(
    width: int,
    height: int,
    line_height: int,
    line_rgba: tuple[int, int, int, int],
) -> bytes:
    """Render a tab image with a bottom underline indicator."""
    rgba = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            idx = (y * width + x) * 4
            if y >= height - line_height:
                rgba[idx] = line_rgba[0]
                rgba[idx + 1] = line_rgba[1]
                rgba[idx + 2] = line_rgba[2]
                rgba[idx + 3] = line_rgba[3]
    return encode_png_rgba(width, height, bytes(rgba))


def render_circle_dot_png(size: int, color_rgba: tuple[int, int, int, int]) -> bytes:
    """Render an anti-aliased circular dot PNG with 4x supersampling."""
    rgba = bytearray(size * size * 4)
    scale = 4
    radius = (size - 1.0) / 2.0
    cx = cy = radius
    for y in range(size):
        for x in range(size):
            total_r, total_g, total_b, total_a = 0, 0, 0, 0
            for sy in range(scale):
                py = y + (sy + 0.5) / scale
                for sx in range(scale):
                    px_val = x + (sx + 0.5) / scale
                    d = math.sqrt((px_val - cx) ** 2 + (py - cy) ** 2)
                    if d <= radius:
                        total_r += color_rgba[0]
                        total_g += color_rgba[1]
                        total_b += color_rgba[2]
                        total_a += color_rgba[3]
            num = scale * scale
            idx = (y * size + x) * 4
            rgba[idx] = int(round(total_r / num))
            rgba[idx + 1] = int(round(total_g / num))
            rgba[idx + 2] = int(round(total_b / num))
            rgba[idx + 3] = int(round(total_a / num))
    return encode_png_rgba(size, size, bytes(rgba))


def render_magnifier_icon_png(size: int, color_rgba: tuple[int, int, int, int]) -> bytes:
    """Render a clean 14-16px magnifier icon with 4x supersampling."""
    rgba = bytearray(size * size * 4)
    scale = 4
    cx = size * 0.40
    cy = size * 0.40
    r_outer = size * 0.34
    r_inner = size * 0.20
    for y in range(size):
        for x in range(size):
            total_r, total_g, total_b, total_a = 0, 0, 0, 0
            for sy in range(scale):
                py = y + (sy + 0.5) / scale
                for sx in range(scale):
                    px_val = x + (sx + 0.5) / scale
                    d_center = math.sqrt((px_val - cx) ** 2 + (py - cy) ** 2)
                    is_ring = r_inner <= d_center <= r_outer

                    hx1, hy1 = cx + r_outer * 0.65, cy + r_outer * 0.65
                    hx2, hy2 = size - 1.5, size - 1.5
                    dx, dy = hx2 - hx1, hy2 - hy1
                    len_sq = dx * dx + dy * dy
                    t = max(0.0, min(1.0, ((px_val - hx1) * dx + (py - hy1) * dy) / len_sq))
                    proj_x = hx1 + t * dx
                    proj_y = hy1 + t * dy
                    d_handle = math.sqrt((px_val - proj_x) ** 2 + (py - proj_y) ** 2)
                    is_handle = d_handle <= 0.9

                    if is_ring or is_handle:
                        total_r += color_rgba[0]
                        total_g += color_rgba[1]
                        total_b += color_rgba[2]
                        total_a += color_rgba[3]
            num = scale * scale
            idx = (y * size + x) * 4
            rgba[idx] = int(round(total_r / num))
            rgba[idx + 1] = int(round(total_g / num))
            rgba[idx + 2] = int(round(total_b / num))
            rgba[idx + 3] = int(round(total_a / num))
    return encode_png_rgba(size, size, bytes(rgba))


def get_kind_image(kind: Kind | str, master: tk.Widget | None = None, size: int = 10) -> tk.PhotoImage:
    """Return an anti-aliased circular dot PhotoImage for the given environment kind."""
    k_str = kind.value if isinstance(kind, Kind) else str(kind)
    actual_size = max(8, px(master, size))
    color = "#6b7280"
    for k, col in KIND_COLORS.items():
        if k == kind or getattr(k, "value", None) == k_str or str(k) == k_str:
            color = col
            break

    if master is not None:
        cache = getattr(master, "_kind_images_cache", None)
        if cache is None:
            cache = {}
            setattr(master, "_kind_images_cache", cache)
        key = (k_str, actual_size)
        if key not in cache:
            dot_png = render_circle_dot_png(actual_size, hex_to_rgba(color))
            img = tk.PhotoImage(master=master, data=dot_png)
            cache[key] = img
        return cache[key]
    else:
        dot_png = render_circle_dot_png(actual_size, hex_to_rgba(color))
        return tk.PhotoImage(data=dot_png)


def get_magnifier_image(master: tk.Widget | None = None, size: int = 14, color: str = "#8f8a7e") -> tk.PhotoImage:
    """Return a clean magnifier icon PhotoImage cached on master."""
    actual_size = max(12, px(master, size))
    if master is not None:
        cache = getattr(master, "_magnifier_images_cache", None)
        if cache is None:
            cache = {}
            setattr(master, "_magnifier_images_cache", cache)
        key = (actual_size, color)
        if key not in cache:
            mag_png = render_magnifier_icon_png(actual_size, hex_to_rgba(color))
            img = tk.PhotoImage(master=master, data=mag_png)
            cache[key] = img
        return cache[key]
    else:
        mag_png = render_magnifier_icon_png(actual_size, hex_to_rgba(color))
        return tk.PhotoImage(data=mag_png)


# ---------------------------------------------------------------------------
# DPI Awareness (Main process startup only)
# ---------------------------------------------------------------------------

def enable_dpi_awareness() -> None:
    """Call in main() before creating App to prevent Windows DPI stretching."""
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Windows Theme Detection & Dark Title Bar
# ---------------------------------------------------------------------------

def is_windows_dark_mode() -> bool:
    """Read Windows AppsUseLightTheme registry key (0 = dark, 1 = light)."""
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return val == 0
    except Exception:
        return False


def set_title_bar_dark_mode(
    window: tk.Tk | tk.Toplevel,
    dark: bool,
    bg_color: str | None = None,
    text_color: str | None = None,
) -> None:
    """Set immersive dark mode and caption colors for window title bar on Windows 10/11."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        dwmapi = ctypes.windll.dwmapi
        hwnd = window.winfo_id()
        parent_hwnd = user32.GetAncestor(hwnd, 2) or user32.GetParent(hwnd) or hwnd

        # DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        val = ctypes.c_int(1 if dark else 0)
        dwmapi.DwmSetWindowAttribute(parent_hwnd, 20, ctypes.byref(val), ctypes.sizeof(val))

        # Windows 11 DWMWA_CAPTION_COLOR (35) and DWMWA_TEXT_COLOR (36)
        if bg_color:
            c = bg_color.lstrip("#")
            if len(c) == 6:
                r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
                colorref_bg = ctypes.c_uint32(r | (g << 8) | (b << 16))
                dwmapi.DwmSetWindowAttribute(parent_hwnd, 35, ctypes.byref(colorref_bg), ctypes.sizeof(colorref_bg))
        if text_color:
            c = text_color.lstrip("#")
            if len(c) == 6:
                r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
                colorref_text = ctypes.c_uint32(r | (g << 8) | (b << 16))
                dwmapi.DwmSetWindowAttribute(parent_hwnd, 36, ctypes.byref(colorref_text), ctypes.sizeof(colorref_text))

        # SWP_NOSIZE(1) | SWP_NOMOVE(2) | SWP_NOZORDER(4) | SWP_NOACTIVATE(16) | SWP_FRAMECHANGED(32) = 0x37
        user32.SetWindowPos(parent_hwnd, 0, 0, 0, 0, 0, 0x0037)
    except Exception:
        pass


def resolve_theme(theme_str: str) -> str:
    """Normalize theme string ('dark', 'light', 'match windows') to 'dark' or 'light'."""
    t = (theme_str or "").strip().lower().replace("_", " ")
    if t == "match windows":
        return "dark" if is_windows_dark_mode() else "light"
    if t in ("dark", "light"):
        return t
    return "dark"


def resolve_palette(palette_str: str) -> str:
    """Normalize palette string to one of the 4 canonical keys."""
    p = (palette_str or "").strip().lower().replace("_", "-").replace(" ", "-")
    if p in PALETTES:
        return p
    for k, name in PALETTE_DISPLAY_NAMES.items():
        if p == name.lower():
            return k
    return "default"


# ---------------------------------------------------------------------------
# Named Fonts Management
# ---------------------------------------------------------------------------

def _get_best_font_family(root: tk.Tk | tk.Toplevel) -> str:
    """Return Segoe UI Variable Text if available, else Segoe UI."""
    try:
        fams = set(tkfont.families(root))
        if "Segoe UI Variable Text" in fams:
            return "Segoe UI Variable Text"
        if "Segoe UI" in fams:
            return "Segoe UI"
    except Exception:
        pass
    return "Segoe UI"


def _setup_named_fonts(root: tk.Tk | tk.Toplevel) -> None:
    """Create persistent named fonts kept on root to avoid garbage collection."""
    named_fonts = getattr(root, "_vd_named_fonts", None)
    if named_fonts is not None:
        return

    existing = set(tkfont.names(root=root))
    if "VDDefault" in existing:
        root._vd_named_fonts = {
            "VDDefault": tkfont.nametofont("VDDefault", root=root),
            "VDHeading": tkfont.nametofont("VDHeading", root=root),
            "VDSection": tkfont.nametofont("VDSection", root=root),
            "VDStatus": tkfont.nametofont("VDStatus", root=root),
        }
        return

    base_family = _get_best_font_family(root)
    # Check if Semibold is recognized
    try:
        test_f = tkfont.Font(root=root, family="Segoe UI Semibold", size=10)
        semibold_family = "Segoe UI Semibold" if "Semibold" in test_f.actual("family") else base_family
    except Exception:
        semibold_family = base_family

    f_default = tkfont.Font(root=root, name="VDDefault", family=base_family, size=10)
    f_heading = tkfont.Font(root=root, name="VDHeading", family=semibold_family, size=9, weight="bold")
    f_section = tkfont.Font(root=root, name="VDSection", family=semibold_family, size=8, weight="bold")
    f_status = tkfont.Font(root=root, name="VDStatus", family=base_family, size=9)

    root._vd_named_fonts = {
        "VDDefault": f_default,
        "VDHeading": f_heading,
        "VDSection": f_section,
        "VDStatus": f_status,
    }


# ---------------------------------------------------------------------------
# Mockup B Image Elements Registration and Updating
# ---------------------------------------------------------------------------

def _init_or_update_image_elements(
    root: tk.Tk | tk.Toplevel,
    ext: dict[str, str],
    is_dark: bool,
) -> None:
    """Create or update 9-slice and custom PhotoImages for ttk elements."""
    style = ttk.Style(root)
    main_root = root
    while hasattr(main_root, "master") and main_root.master:
        main_root = main_root.master

    imgs = getattr(main_root, "_vd_element_images", None)
    first_time = imgs is None
    if first_time:
        imgs = {}
        main_root._vd_element_images = imgs
    root._vd_element_images = imgs

    current_palette_key = (ext["bg"], ext["panel"], ext["line"], ext["amber"], ext["danger"], is_dark)
    if not first_time and getattr(main_root, "_vd_last_element_key", None) == current_palette_key:
        return
    main_root._vd_last_element_key = current_palette_key

    # Button sizes (30x30 with radius 7 and border 8 so center slice is clean)
    r_btn = 7.0
    bw_btn = 1.0
    w_btn = 30
    h_btn = 30
    bg_rgba = hex_to_rgba(ext["bg"])

    # Render button states
    b_norm = render_rounded_box_png(w_btn, h_btn, r_btn, bw_btn, hex_to_rgba(ext["panel"]), hex_to_rgba(ext["line"]), bg_rgba)
    b_act = render_rounded_box_png(w_btn, h_btn, r_btn, bw_btn, hex_to_rgba(ext["panel_hover"]), hex_to_rgba(ext["line_hover"]), bg_rgba)
    b_press = render_rounded_box_png(w_btn, h_btn, r_btn, bw_btn, hex_to_rgba(ext["panel_pressed"]), hex_to_rgba(ext["line_hover"]), bg_rgba)
    b_dis = render_rounded_box_png(w_btn, h_btn, r_btn, bw_btn, hex_to_rgba(ext["panel"], 120), hex_to_rgba(ext["line"], 100), bg_rgba)

    # Accent buttons (Primary Open terminal)
    acc_norm = render_rounded_box_png(w_btn, h_btn, r_btn, bw_btn, hex_to_rgba(ext["amber"]), hex_to_rgba(ext["amber"]), bg_rgba)
    acc_act = render_rounded_box_png(w_btn, h_btn, r_btn, bw_btn, hex_to_rgba(ext["amber_hover"]), hex_to_rgba(ext["amber_hover"]), bg_rgba)
    acc_press = render_rounded_box_png(w_btn, h_btn, r_btn, bw_btn, hex_to_rgba(ext["amber_pressed"]), hex_to_rgba(ext["amber_pressed"]), bg_rgba)
    acc_dis = render_rounded_box_png(w_btn, h_btn, r_btn, bw_btn, hex_to_rgba(ext["muted"], 120), hex_to_rgba(ext["muted"], 100), bg_rgba)

    # Split Left Accent Button (Top-left & bottom-left rounded, right straight)
    sleft_norm = render_rounded_box_png(w_btn, h_btn, r_btn, bw_btn, hex_to_rgba(ext["amber"]), hex_to_rgba(ext["amber"]), bg_rgba, corners=(True, False, False, True))
    sleft_act = render_rounded_box_png(w_btn, h_btn, r_btn, bw_btn, hex_to_rgba(ext["amber_hover"]), hex_to_rgba(ext["amber_hover"]), bg_rgba, corners=(True, False, False, True))
    sleft_press = render_rounded_box_png(w_btn, h_btn, r_btn, bw_btn, hex_to_rgba(ext["amber_pressed"]), hex_to_rgba(ext["amber_pressed"]), bg_rgba, corners=(True, False, False, True))

    # Split Right Accent Menubutton (Top-right & bottom-right rounded, left straight, 1px amber-ink ~30% divider)
    divider_rgba = hex_to_rgba(blend_hex(ext["amber"], ext["amber_ink"], 0.30))
    sright_norm = render_rounded_box_png(w_btn, h_btn, r_btn, bw_btn, hex_to_rgba(ext["amber"]), hex_to_rgba(ext["amber"]), bg_rgba, corners=(False, True, True, False), left_divider_rgba=divider_rgba)
    sright_act = render_rounded_box_png(w_btn, h_btn, r_btn, bw_btn, hex_to_rgba(ext["amber_hover"]), hex_to_rgba(ext["amber_hover"]), bg_rgba, corners=(False, True, True, False), left_divider_rgba=divider_rgba)
    sright_press = render_rounded_box_png(w_btn, h_btn, r_btn, bw_btn, hex_to_rgba(ext["amber_pressed"]), hex_to_rgba(ext["amber_pressed"]), bg_rgba, corners=(False, True, True, False), left_divider_rgba=divider_rgba)

    # Danger Button (Neutral button with red text, turns red on hover)
    d_norm = render_rounded_box_png(w_btn, h_btn, r_btn, bw_btn, hex_to_rgba(ext["panel"]), hex_to_rgba(ext["line"]), bg_rgba)
    d_act = render_rounded_box_png(w_btn, h_btn, r_btn, bw_btn, hex_to_rgba(ext["danger_hover"]), hex_to_rgba(ext["danger_hover"]), bg_rgba)
    d_press = render_rounded_box_png(w_btn, h_btn, r_btn, bw_btn, hex_to_rgba(ext["danger_pressed"]), hex_to_rgba(ext["danger_pressed"]), bg_rgba)
    d_dis = render_rounded_box_png(w_btn, h_btn, r_btn, bw_btn, hex_to_rgba(ext["panel"], 120), hex_to_rgba(ext["line"], 100), bg_rgba)

    # Entry Field (Input box with focus ring)
    e_norm = render_rounded_box_png(30, 30, 7.0, 1.0, hex_to_rgba(ext["panel"]), hex_to_rgba(ext["line"]), bg_rgba)
    e_focus = render_rounded_box_png(30, 30, 7.0, 1.0, hex_to_rgba(ext["panel"]), hex_to_rgba(ext["amber"]), bg_rgba)
    e_hover = render_rounded_box_png(30, 30, 7.0, 1.0, hex_to_rgba(ext["panel"]), hex_to_rgba(ext["line_hover"]), bg_rgba)

    # Card background (radius 10)
    # Card background (rendered 256x256 so centre is 234x234, cheap tiling and 4x fewer pixels)
    card_png = render_rounded_box_png(256, 256, 10.0, 1.0, hex_to_rgba(ext["panel"]), hex_to_rgba(ext["line"]), bg_rgba)

    # Tab Underline
    tab_sel_png = render_underline_tab_png(20, 24, 2, hex_to_rgba(ext["amber"]))
    tab_norm_png = render_underline_tab_png(20, 24, 2, (0, 0, 0, 0))

    # Thin Scrollbars (width 8px)
    # In default light amber mode, make the scrollbars vibrant orange (the ones that let you scroll)
    if not is_dark and ext.get("amber") == "#985d00":
        sb_norm_color = "#f97316"  # vibrant orange
        sb_act_color = "#ea580c"   # deeper orange on active/hover
    else:
        sb_norm_color = ext["line_hover"]
        sb_act_color = ext["muted"]

    sb_v_norm = render_rounded_box_png(8, 20, 4.0, 0.0, hex_to_rgba(sb_norm_color), (0, 0, 0, 0))
    sb_v_act = render_rounded_box_png(8, 20, 4.0, 0.0, hex_to_rgba(sb_act_color), (0, 0, 0, 0))
    sb_h_norm = render_rounded_box_png(20, 8, 4.0, 0.0, hex_to_rgba(sb_norm_color), (0, 0, 0, 0))
    sb_h_act = render_rounded_box_png(20, 8, 4.0, 0.0, hex_to_rgba(sb_act_color), (0, 0, 0, 0))

    png_map = {
        "btn_norm": b_norm,
        "btn_act": b_act,
        "btn_press": b_press,
        "btn_dis": b_dis,
        "acc_norm": acc_norm,
        "acc_act": acc_act,
        "acc_press": acc_press,
        "acc_dis": acc_dis,
        "sleft_norm": sleft_norm,
        "sleft_act": sleft_act,
        "sleft_press": sleft_press,
        "sright_norm": sright_norm,
        "sright_act": sright_act,
        "sright_press": sright_press,
        "d_norm": d_norm,
        "d_act": d_act,
        "d_press": d_press,
        "d_dis": d_dis,
        "e_norm": e_norm,
        "e_focus": e_focus,
        "e_hover": e_hover,
        "card_bg": card_png,
        "tab_sel": tab_sel_png,
        "tab_norm": tab_norm_png,
        "sb_v_norm": sb_v_norm,
        "sb_v_act": sb_v_act,
        "sb_h_norm": sb_h_norm,
        "sb_h_act": sb_h_act,
    }

    if first_time:
        for k_name, p_data in png_map.items():
            imgs[k_name] = tk.PhotoImage(master=main_root, data=p_data)
    else:
        for k_name, p_data in png_map.items():
            imgs[k_name].configure(data=p_data)

    existing_elements = set(style.element_names())
    if "VD.Button" not in existing_elements:
        style.element_create(
            "VD.Button",
            "image",
            imgs["btn_norm"],
            ("pressed", imgs["btn_press"]),
            ("active", imgs["btn_act"]),
            ("disabled", imgs["btn_dis"]),
            border=8,
            sticky="nsew",
        )
        style.element_create(
            "VD.AccentButton",
            "image",
            imgs["acc_norm"],
            ("pressed", imgs["acc_press"]),
            ("active", imgs["acc_act"]),
            ("disabled", imgs["acc_dis"]),
            border=8,
            sticky="nsew",
        )
        style.element_create(
            "VD.SplitLeftButton",
            "image",
            imgs["sleft_norm"],
            ("pressed", imgs["sleft_press"]),
            ("active", imgs["sleft_act"]),
            border=(8, 8, 0, 8),
            sticky="nsew",
        )
        style.element_create(
            "VD.SplitRightButton",
            "image",
            imgs["sright_norm"],
            ("pressed", imgs["sright_press"]),
            ("active", imgs["sright_act"]),
            border=(1, 8, 8, 8),
            sticky="nsew",
        )
        style.element_create(
            "VD.DangerButton",
            "image",
            imgs["d_norm"],
            ("pressed", imgs["d_press"]),
            ("active", imgs["d_act"]),
            ("disabled", imgs["d_dis"]),
            border=8,
            sticky="nsew",
        )
        style.element_create(
            "VD.EntryField",
            "image",
            imgs["e_norm"],
            ("focus", imgs["e_focus"]),
            ("active", imgs["e_hover"]),
            border=8,
            sticky="nsew",
        )
        style.element_create("VD.CardBg", "image", imgs["card_bg"], border=11, sticky="nsew")
        style.element_create(
            "VD.Tab",
            "image",
            imgs["tab_norm"],
            ("selected", imgs["tab_sel"]),
            border=(0, 0, 0, 2),
            sticky="nsew",
        )
        style.element_create(
            "VD.Vertical.Scrollbar.thumb",
            "image",
            imgs["sb_v_norm"],
            ("active", imgs["sb_v_act"]),
            border=(0, 4, 0, 4),
            sticky="nswe",
        )
        style.element_create(
            "VD.Horizontal.Scrollbar.thumb",
            "image",
            imgs["sb_h_norm"],
            ("active", imgs["sb_h_act"]),
            border=(4, 0, 4, 0),
            sticky="nswe",
        )



# ---------------------------------------------------------------------------
# Core Theme Application
# ---------------------------------------------------------------------------

def apply_theme(root: tk.Tk | tk.Toplevel, theme: str = "dark", palette: str = "default") -> None:
    """Restyle all ttk and tk widgets live to match the requested theme and palette."""
    resolved_theme = resolve_theme(theme)
    resolved_palette = resolve_palette(palette)
    pal_data = PALETTES.get(resolved_palette, PALETTES["default"])
    colors = pal_data[resolved_theme]
    is_dark = resolved_theme == "dark"
    ext = get_extended_colors(colors, is_dark)

    style = ttk.Style(root)
    try:
        if style.theme_use() != "clam":
            style.theme_use("clam")
    except Exception:
        pass

    _setup_named_fonts(root)
    _init_or_update_image_elements(root, ext, is_dark)

    bg_color = colors["bg"]
    panel_color = colors["panel"]
    line_color = colors["line"]
    text_color = colors["text"]
    muted_color = colors["muted"]
    amber_color = colors["amber"]
    amber_ink = colors["amber_ink"]
    selection_color = colors["selection"]
    danger_color = colors["danger"]

    # Configure root window background
    try:
        root.configure(background=bg_color)
    except Exception:
        pass

    # 1. Base Frames and Labels
    style.configure("TFrame", background=bg_color)
    style.configure("TLabel", background=bg_color, foreground=text_color, font="VDDefault")

    # Card.TFrame
    style.layout(
        "Card.TFrame",
        [("VD.CardBg", {"sticky": "nsew", "children": [("Frame.padding", {"sticky": "nsew"})]})],
    )
    style.configure("Card.TFrame", background=bg_color)

    # CardInner.TFrame (Plain panel background for inner frames inside cards, no image element)
    style.layout("CardInner.TFrame", [("Frame.padding", {"sticky": "nsew"})])
    style.configure("CardInner.TFrame", background=colors["panel"])

    # Section Labels and Muted Labels
    style.configure("Section.TLabel", font="VDSection", foreground=muted_color, background=panel_color)
    style.configure("Muted.TLabel", font="VDStatus", foreground=muted_color, background=panel_color)

    # 2. Buttons
    btn_pad = (px(root, 11), px(root, 6))

    # Standard TButton
    style.layout(
        "TButton",
        [
            (
                "VD.Button",
                {
                    "children": [
                        (
                            "Button.padding",
                            {"children": [("Button.label", {"sticky": "nswe"})], "sticky": "nswe"},
                        )
                    ],
                    "sticky": "nswe",
                },
            )
        ],
    )
    style.configure(
        "TButton",
        padding=btn_pad,
        font="VDDefault",
        background=panel_color,
        bordercolor=line_color,
        lightcolor=line_color,
        darkcolor=line_color,
    )
    style.map(
        "TButton",
        background=[("active", ext["panel_hover"]), ("pressed", ext["panel_pressed"]), ("!disabled", panel_color)],
        bordercolor=[("active", line_color), ("!disabled", line_color)],
        lightcolor=[("active", line_color), ("!disabled", line_color)],
        darkcolor=[("active", line_color), ("!disabled", line_color)],
        foreground=[("disabled", muted_color), ("!disabled", text_color)],
    )

    # Accent.TButton (Primary: Open terminal)
    style.layout(
        "Accent.TButton",
        [
            (
                "VD.AccentButton",
                {
                    "children": [
                        (
                            "Button.padding",
                            {"children": [("Button.label", {"sticky": "nswe"})], "sticky": "nswe"},
                        )
                    ],
                    "sticky": "nswe",
                },
            )
        ],
    )
    style.configure("Accent.TButton", padding=btn_pad, font="VDHeading")
    style.map(
        "Accent.TButton",
        foreground=[("disabled", muted_color), ("!disabled", amber_ink)],
    )

    # SplitLeft.Accent.TButton
    style.layout(
        "SplitLeft.Accent.TButton",
        [
            (
                "VD.SplitLeftButton",
                {
                    "children": [
                        (
                            "Button.padding",
                            {"children": [("Button.label", {"sticky": "nswe"})], "sticky": "nswe"},
                        )
                    ],
                    "sticky": "nswe",
                },
            )
        ],
    )
    style.configure("SplitLeft.Accent.TButton", padding=(px(root, 11), px(root, 6)), font="VDHeading")
    style.map("SplitLeft.Accent.TButton", foreground=[("disabled", muted_color), ("!disabled", amber_ink)])

    # SplitRight.Accent.TMenubutton
    style.layout(
        "SplitRight.Accent.TMenubutton",
        [
            (
                "VD.SplitRightButton",
                {
                    "children": [
                        (
                            "Menubutton.padding",
                            {"children": [("Menubutton.label", {"sticky": "nswe"})], "sticky": "nswe"},
                        )
                    ],
                    "sticky": "nswe",
                },
            )
        ],
    )
    style.configure("SplitRight.Accent.TMenubutton", padding=(px(root, 7), px(root, 6)), font="VDHeading")
    style.map("SplitRight.Accent.TMenubutton", foreground=[("disabled", muted_color), ("!disabled", amber_ink)])

    # Danger.TButton (Neutral with red text, turns red on hover)
    style.layout(
        "Danger.TButton",
        [
            (
                "VD.DangerButton",
                {
                    "children": [
                        (
                            "Button.padding",
                            {"children": [("Button.label", {"sticky": "nswe"})], "sticky": "nswe"},
                        )
                    ],
                    "sticky": "nswe",
                },
            )
        ],
    )
    style.configure("Danger.TButton", padding=btn_pad, font="VDDefault")
    style.map(
        "Danger.TButton",
        foreground=[
            ("disabled", muted_color),
            ("active", "#ffffff"),
            ("pressed", "#ffffff"),
            ("!disabled", danger_color),
        ],
    )

    # 3. Entries
    style.layout(
        "TEntry",
        [
            (
                "VD.EntryField",
                {
                    "children": [
                        (
                            "Entry.padding",
                            {"children": [("Entry.textarea", {"sticky": "nswe"})], "sticky": "nswe"},
                        )
                    ],
                    "sticky": "nswe",
                },
            )
        ],
    )
    style.configure(
        "TEntry",
        padding=(px(root, 8), px(root, 6)),
        font="VDDefault",
        foreground=text_color,
        fieldbackground=panel_color,
        background=panel_color,
        bordercolor=line_color,
        lightcolor=line_color,
        darkcolor=line_color,
        insertcolor=text_color,
    )
    style.map(
        "TEntry",
        bordercolor=[("focus", colors["amber"]), ("!disabled", line_color)],
        lightcolor=[("focus", colors["amber"]), ("!disabled", line_color)],
        darkcolor=[("focus", colors["amber"]), ("!disabled", line_color)],
        background=[("!disabled", panel_color)],
        fieldbackground=[("!disabled", panel_color)],
    )

    # 4. Notebook (Underline tabs, no boxed body)
    style.layout("TNotebook", [("Notebook.client", {"sticky": "nsew"})])
    style.configure(
        "TNotebook",
        background=bg_color,
        bordercolor=bg_color,
        darkcolor=bg_color,
        lightcolor=bg_color,
        borderwidth=0,
        tabmargins=[0, 0, 0, 0],
    )

    style.layout(
        "TNotebook.Tab",
        [
            (
                "VD.Tab",
                {
                    "children": [
                        (
                            "Notebook.padding",
                            {"children": [("Notebook.label", {"sticky": "nswe"})], "sticky": "nswe"},
                        )
                    ],
                    "sticky": "nswe",
                },
            )
        ],
    )
    style.configure(
        "TNotebook.Tab",
        padding=(px(root, 14), px(root, 6)),
        font="VDHeading",
        background=bg_color,
        bordercolor=bg_color,
        lightcolor=bg_color,
        darkcolor=bg_color,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", bg_color), ("!selected", bg_color)],
        bordercolor=[("selected", bg_color), ("!selected", bg_color)],
        lightcolor=[("selected", bg_color), ("!selected", bg_color)],
        darkcolor=[("selected", bg_color), ("!selected", bg_color)],
        foreground=[("selected", text_color), ("!selected", muted_color)],
    )

    # 5. Thin Scrollbars (8px, NO arrows!)
    style.layout(
        "Vertical.TScrollbar",
        [
            (
                "Vertical.Scrollbar.trough",
                {
                    "children": [("VD.Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})],
                    "sticky": "ns",
                },
            )
        ],
    )
    style.configure(
        "Vertical.TScrollbar",
        width=px(root, 8),
        background=panel_color,
        troughcolor=panel_color,
        borderwidth=0,
        arrowsize=0,
    )

    style.layout(
        "Horizontal.TScrollbar",
        [
            (
                "Horizontal.Scrollbar.trough",
                {
                    "children": [("VD.Horizontal.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})],
                    "sticky": "we",
                },
            )
        ],
    )
    style.configure(
        "Horizontal.TScrollbar",
        width=px(root, 8),
        background=panel_color,
        troughcolor=panel_color,
        borderwidth=0,
        arrowsize=0,
    )

    # 6. Treeview
    style.configure(
        "Treeview",
        background=panel_color,
        fieldbackground=panel_color,
        foreground=text_color,
        bordercolor=panel_color,
        lightcolor=panel_color,
        darkcolor=panel_color,
        font="VDDefault",
        rowheight=px(root, 30),
        borderwidth=0,
        relief="flat",
    )
    style.map(
        "Treeview",
        background=[("selected", selection_color)],
        foreground=[("selected", text_color)],
    )

    style.configure(
        "Treeview.Heading",
        background=panel_color,
        foreground=muted_color,
        font="VDHeading",
        borderwidth=0,
        relief="flat",
        padding=(px(root, 6), px(root, 6)),
    )
    style.map(
        "Treeview.Heading",
        background=[("active", ext["panel_hover"])],
        foreground=[("active", text_color)],
    )

    # 7. Paned Window
    style.configure(
        "TPanedwindow",
        background=bg_color,
        bordercolor=bg_color,
        lightcolor=bg_color,
        darkcolor=bg_color,
    )
    style.configure(
        "Sash",
        background=bg_color,
        bordercolor=bg_color,
        lightcolor=bg_color,
        darkcolor=bg_color,
        sashthickness=px(root, 12),
        gripcount=0,
    )
    style.configure(
        "TScrollbar",
        background=panel_color,
        troughcolor=panel_color,
        bordercolor=panel_color,
        lightcolor=panel_color,
        darkcolor=panel_color,
        arrowcolor=muted_color,
        gripcount=0,
    )

    # 8. Status Bar
    style.configure("StatusBar.TFrame", background=bg_color, relief="flat", borderwidth=0)
    style.configure("Status.TLabel", font="VDStatus", foreground=text_color, background=bg_color)
    style.configure("StatusMuted.TLabel", font="VDStatus", foreground=muted_color, background=bg_color)

    # 9. Combobox & Menubutton
    style.configure(
        "TMenubutton",
        background=panel_color,
        foreground=text_color,
        bordercolor=line_color,
        lightcolor=line_color,
        darkcolor=line_color,
        font="VDDefault",
        padding=(px(root, 8), px(root, 6)),
        relief="flat",
        borderwidth=0,
    )
    style.map(
        "TMenubutton",
        background=[("active", ext["panel_hover"])],
        foreground=[("active", text_color)],
        bordercolor=[("active", line_color)],
        lightcolor=[("active", line_color)],
        darkcolor=[("active", line_color)],
    )

    style.configure(
        "TCombobox",
        background=panel_color,
        foreground=text_color,
        fieldbackground=panel_color,
        darkcolor=line_color,
        lightcolor=line_color,
        bordercolor=line_color,
        arrowcolor=text_color,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", panel_color)],
        selectbackground=[("readonly", selection_color)],
        selectforeground=[("readonly", text_color)],
    )

    # 10. Classic Tk Widgets
    root.option_add("*TCombobox*Listbox.background", panel_color)
    root.option_add("*TCombobox*Listbox.foreground", text_color)
    root.option_add("*TCombobox*Listbox.selectBackground", selection_color)
    root.option_add("*TCombobox*Listbox.selectForeground", text_color)
    root.option_add("*Menu.background", panel_color)
    root.option_add("*Menu.foreground", text_color)
    root.option_add("*Menu.activeBackground", selection_color)
    root.option_add("*Menu.activeForeground", text_color)
    root.option_add("*Text.background", panel_color)
    root.option_add("*Text.foreground", text_color)
    root.option_add("*Text.selectBackground", selection_color)
    root.option_add("*Text.selectForeground", text_color)

    # 11. Immersive Title Bar (read dynamic info at call time, bind once per window)
    root._vd_theme_info = (is_dark, bg_color, text_color)

    def _on_root_map(event: tk.Event) -> None:
        if getattr(event, "widget", None) is not root:
            return
        info = getattr(root, "_vd_theme_info", None)
        if info:
            set_title_bar_dark_mode(root, info[0], bg_color=info[1], text_color=info[2])

    if not getattr(root, "_vd_map_bound", False):
        root.bind("<Map>", _on_root_map, add="+")
        root._vd_map_bound = True

    if root.winfo_ismapped():
        set_title_bar_dark_mode(root, is_dark, bg_color=bg_color, text_color=text_color)


def style_window(window: tk.Tk | tk.Toplevel) -> None:
    """Style a Toplevel dialog window to match the current theme."""
    parent = getattr(window, "master", None) or getattr(window, "parent", None)
    current_theme = getattr(parent, "current_theme", "dark")
    current_palette = getattr(parent, "current_palette", "default")
    resolved_theme = resolve_theme(current_theme)
    resolved_palette = resolve_palette(current_palette)
    colors = PALETTES.get(resolved_palette, PALETTES["default"])[resolved_theme]
    is_dark = resolved_theme == "dark"

    bg_color = colors["bg"]
    panel_color = colors["panel"]
    text_color = colors["text"]
    selection_color = colors["selection"]

    try:
        window.configure(background=bg_color)
    except Exception:
        pass

    window._vd_theme_info = (is_dark, bg_color, text_color)

    def _on_sub_map(event: tk.Event) -> None:
        if getattr(event, "widget", None) is not window:
            return
        info = getattr(window, "_vd_theme_info", None)
        if info:
            set_title_bar_dark_mode(window, info[0], bg_color=info[1], text_color=info[2])

    if not getattr(window, "_vd_map_bound", False):
        window.bind("<Map>", _on_sub_map, add="+")
        window._vd_map_bound = True

    if window.winfo_ismapped():
        set_title_bar_dark_mode(window, is_dark, bg_color=bg_color, text_color=text_color)

    def _restyle_children(w: tk.Widget) -> None:
        for child in w.winfo_children():
            if isinstance(child, tk.Text):
                child.configure(
                    background=panel_color,
                    foreground=text_color,
                    insertbackground=text_color,
                    selectbackground=selection_color,
                    selectforeground=text_color,
                    relief="flat",
                    borderwidth=1,
                    highlightthickness=1,
                    highlightbackground=colors["line"],
                    highlightcolor=colors["amber"],
                )
            elif isinstance(child, tk.Menu):
                child.configure(
                    background=panel_color,
                    foreground=text_color,
                    activebackground=selection_color,
                    activeforeground=text_color,
                )
            _restyle_children(child)

    _restyle_children(window)
