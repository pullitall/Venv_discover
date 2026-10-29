"""Venv Discover logo & icon generator.

Standard library only: no Pillow, zero external dependencies.
Generates:
  - venvdiscover/assets/venvdiscover.ico (14 sizes: 16, 20, 24, 30, 32, 36, 40, 48, 60, 64, 72, 96, 128, 256)
  - venvdiscover/assets/venvdiscover-256.png
  - docs/logo-preview.png (contact sheet: 1x on 4 backgrounds + 8x zoom on 16, 24, 32)

Deterministic: running twice produces byte-identical files.
"""

from __future__ import annotations

import math
import os
import struct
import zlib

SIZES: list[int] = [16, 20, 24, 30, 32, 36, 40, 48, 60, 64, 72, 96, 128, 256]

# Colors from the app's palette
C_BG = (26, 26, 23, 255)             # #1a1a17 - Dark rounded-square tile
C_BORDER = (46, 45, 41, 255)         # #2e2d29 - 1px edge from 32px up
C_AMBER = (255, 178, 36, 255)        # #ffb224 - Main amber mark
C_AMBER_LIGHT = (255, 203, 107, 255) # #ffcb6b - Lighter amber highlight (cube top face)
C_AMBER_DARK = (199, 131, 10, 255)   # #c7830a - Darker amber shadow (cube right face)

# Contact sheet backgrounds
BG_DARK_TASKBAR = (32, 32, 32, 255)     # #202020
BG_LIGHT_TASKBAR = (243, 243, 243, 255) # #f3f3f3
BG_APP_DARK = (17, 17, 15, 255)         # #11110f
BG_APP_LIGHT = (244, 241, 232, 255)     # #f4f1e8


def make_png(width: int, height: int, rgba_bytes: bytes) -> bytes:
    """Create a valid PNG file from raw RGBA bytes."""
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    ihdr_crc = struct.pack(">I", zlib.crc32(b"IHDR" + ihdr_data))
    ihdr = struct.pack(">I", len(ihdr_data)) + b"IHDR" + ihdr_data + ihdr_crc

    raw_lines = bytearray()
    row_len = width * 4
    for y in range(height):
        raw_lines.append(0)  # filter None
        raw_lines.extend(rgba_bytes[y * row_len : (y + 1) * row_len])

    comp = zlib.compress(bytes(raw_lines), level=9)
    idat_crc = struct.pack(">I", zlib.crc32(b"IDAT" + comp))
    idat = struct.pack(">I", len(comp)) + b"IDAT" + comp + idat_crc

    iend_crc = struct.pack(">I", zlib.crc32(b"IEND"))
    iend = struct.pack(">I", 0) + b"IEND" + iend_crc

    return sig + ihdr + idat + iend


def make_ico(png_dict: dict[int, bytes]) -> bytes:
    """Create a valid ICO file with PNG entries from a dict mapping size to png_bytes."""
    sizes = sorted(png_dict.keys())
    count = len(sizes)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count
    entries = []
    images = []

    for s in sizes:
        png_data = png_dict[s]
        w = 0 if s == 256 else s
        h = 0 if s == 256 else s
        size_bytes = len(png_data)
        entry = struct.pack(
            "<BBBBHHII",
            w,
            h,
            0,  # color count
            0,  # reserved
            1,  # planes
            32,  # bit count
            size_bytes,
            offset,
        )
        entries.append(entry)
        images.append(png_data)
        offset += size_bytes

    return header + b"".join(entries) + b"".join(images)


def _grid_to_rgba(size: int, grid: list[str]) -> bytes:
    pixels = bytearray(size * size * 4)
    color_map = {
        ".": (0, 0, 0, 0),
        "#": C_BG,
        "+": C_BORDER,
        "o": C_AMBER,
        "@": C_AMBER_LIGHT,
        "x": C_AMBER_DARK,
    }
    for y in range(size):
        row = grid[y]
        for x in range(size):
            col = color_map.get(row[x], (0, 0, 0, 0))
            idx = (y * size + x) * 4
            pixels[idx] = col[0]
            pixels[idx + 1] = col[1]
            pixels[idx + 2] = col[2]
            pixels[idx + 3] = col[3]
    return bytes(pixels)


def render_icon_16() -> bytes:
    """16x16 hand-tuned pixel map: bold 2px lens & handle, 2x2 amber dot, aligned to whole pixels."""
    grid = [
        ".##############.",
        "################",
        "####ooooo#######",
        "###ooooooo######",
        "##ooo###ooo#####",
        "##oo#####oo#####",
        "##oo##@@##oo####",
        "##oo##@@##oo####",
        "##oo#####oo#####",
        "###ooo##ooo#####",
        "####oooooooo####",
        "##########ooo###",
        "###########ooo##",
        "############oo##",
        "################",
        ".##############.",
    ]
    return _grid_to_rgba(16, grid)


def render_icon_20() -> bytes:
    """20x20 hand-tuned pixel map: bold 2px lens ring & handle, amber dot."""
    grid = [
        "..################..",
        ".##################.",
        "#####oooooo#########",
        "####oooooooo########",
        "###ooo####ooo#######",
        "##ooo######ooo######",
        "##oo########oo######",
        "##oo###@@###oo######",
        "##oo###@@###oo######",
        "##oo########oo######",
        "##ooo######ooo######",
        "###ooo####oooo######",
        "####ooooooooooo#####",
        "#####ooooooooooo####",
        "############ooooo###",
        "#############ooooo##",
        "##############oooo##",
        "###############ooo##",
        ".##################.",
        "..################..",
    ]
    return _grid_to_rgba(20, grid)


def render_icon_24() -> bytes:
    """24x24 hand-tuned pixel map: 1px border, bold lens, amber diamond cube."""
    grid = [
        "...##################...",
        ".######################.",
        ".######ooooo###########.",
        "#####ooooooooo##########",
        "####ooooo#ooooo#########",
        "###ooo#######ooo########",
        "###oo#########oo########",
        "##ooo####@####ooo#######",
        "##ooo###@@@###ooo#######",
        "##oo###@@@@@###oo#######",
        "##ooo###@@@###ooo#######",
        "##ooo####@####ooo#######",
        "###oo#########oo########",
        "###ooo#######ooo########",
        "####ooooo#ooooooo#######",
        "#####ooooooooooooo######",
        "#######ooooo##ooooo#####",
        "###############ooooo####",
        "################ooooo###",
        "#################ooooo##",
        "##################oooo##",
        ".##################oo##.",
        ".######################.",
        "...##################...",
    ]
    return _grid_to_rgba(24, grid)


def _dist_segment(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
    dx = x2 - x1
    dy = y2 - y1
    l2 = dx * dx + dy * dy
    if l2 == 0.0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / l2))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return math.hypot(px - proj_x, py - proj_y)


def _sdf_box(px: float, py: float, bx: float, by: float, r: float) -> float:
    qx = abs(px) - bx + r
    qy = abs(py) - by + r
    return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r


def _point_in_poly(px: float, py: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if ((y1 > py) != (y2 > py)) and (px < (x2 - x1) * (py - y1) / (y2 - y1 + 1e-12) + x1):
            inside = not inside
    return inside


def render_icon(size: int) -> bytes:
    """Render RGBA bytes for the Venv Discover logo at the given size."""
    if size == 16:
        return render_icon_16()
    elif size == 20:
        return render_icon_20()
    elif size == 24:
        return render_icon_24()

    SS = 4
    pixels = bytearray(size * size * 4)

    c = size / 2.0
    half_box = size * 0.45
    corner_r = size * 0.22

    lens_cx = size * 0.42
    lens_cy = size * 0.42
    lens_r_out = size * 0.25
    lens_r_in = size * 0.165
    handle_w = size * 0.075

    h_angle = math.radians(45)
    h_start_x = lens_cx + (lens_r_out - 1.0) * math.cos(h_angle)
    h_start_y = lens_cy + (lens_r_out - 1.0) * math.sin(h_angle)
    h_end_x = size * 0.77
    h_end_y = size * 0.77

    cube_s = size * 0.125
    vt = (lens_cx, lens_cy - cube_s * 0.95)
    vrt = (lens_cx + cube_s * 0.866, lens_cy - cube_s * 0.45)
    vrb = (lens_cx + cube_s * 0.866, lens_cy + cube_s * 0.55)
    vb = (lens_cx, lens_cy + cube_s * 1.05)
    vlb = (lens_cx - cube_s * 0.866, lens_cy + cube_s * 0.55)
    vlt = (lens_cx - cube_s * 0.866, lens_cy - cube_s * 0.45)
    vc = (lens_cx, lens_cy + cube_s * 0.05)

    poly_top = [vt, vrt, vc, vlt]
    poly_left = [vlt, vc, vb, vlb]
    poly_right = [vc, vrt, vrb, vb]

    border_width = 1.0 if size >= 32 else 0.0

    for y in range(size):
        for x in range(size):
            r_acc, g_acc, b_acc, a_acc = 0, 0, 0, 0
            for sy in range(SS):
                sub_y = y + (sy + 0.5) / SS
                for sx in range(SS):
                    sub_x = x + (sx + 0.5) / SS

                    d_tile = _sdf_box(sub_x - c, sub_y - c, half_box, half_box, corner_r)
                    if d_tile > 0.0:
                        continue

                    if border_width > 0.0 and d_tile > -border_width:
                        pix = C_BORDER
                    else:
                        pix = C_BG

                    d_lens = math.hypot(sub_x - lens_cx, sub_y - lens_cy)
                    is_lens = (lens_r_in <= d_lens <= lens_r_out)
                    d_h = _dist_segment(sub_x, sub_y, h_start_x, h_start_y, h_end_x, h_end_y)
                    is_h = (d_h <= handle_w)

                    if is_lens or is_h:
                        pix = C_AMBER
                    elif size >= 32:
                        if _point_in_poly(sub_x, sub_y, poly_top):
                            pix = C_AMBER_LIGHT
                        elif _point_in_poly(sub_x, sub_y, poly_left):
                            pix = C_AMBER
                        elif _point_in_poly(sub_x, sub_y, poly_right):
                            pix = C_AMBER_DARK
                    else:
                        dx = abs(sub_x - lens_cx)
                        dy = abs(sub_y - lens_cy)
                        if dx + dy <= size * 0.08:
                            pix = C_AMBER

                    r_acc += pix[0]
                    g_acc += pix[1]
                    b_acc += pix[2]
                    a_acc += pix[3]

            n = SS * SS
            idx = (y * size + x) * 4
            pixels[idx] = r_acc // n
            pixels[idx + 1] = g_acc // n
            pixels[idx + 2] = b_acc // n
            pixels[idx + 3] = a_acc // n

    return bytes(pixels)


def _blend_pixel(src: tuple[int, int, int, int], dst: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    sa = src[3] / 255.0
    if sa <= 0.0:
        return dst
    if sa >= 1.0:
        return src
    da = dst[3] / 255.0
    out_a = sa + da * (1.0 - sa)
    if out_a <= 0.0:
        return (0, 0, 0, 0)
    r = int(round((src[0] * sa + dst[0] * da * (1.0 - sa)) / out_a))
    g = int(round((src[1] * sa + dst[1] * da * (1.0 - sa)) / out_a))
    b = int(round((src[2] * sa + dst[2] * da * (1.0 - sa)) / out_a))
    return (r, g, b, int(round(out_a * 255)))


def _blit(dst_bytes: bytearray, dst_w: int, dst_h: int,
          src_bytes: bytes, src_w: int, src_h: int,
          x_off: int, y_off: int, scale: int = 1) -> None:
    for sy in range(src_h):
        for sx in range(src_w):
            s_idx = (sy * src_w + sx) * 4
            src_col = (src_bytes[s_idx], src_bytes[s_idx + 1], src_bytes[s_idx + 2], src_bytes[s_idx + 3])
            if src_col[3] == 0:
                continue
            for dy in range(scale):
                py = y_off + sy * scale + dy
                if not (0 <= py < dst_h):
                    continue
                for dx in range(scale):
                    px = x_off + sx * scale + dx
                    if not (0 <= px < dst_w):
                        continue
                    d_idx = (py * dst_w + px) * 4
                    dst_col = (dst_bytes[d_idx], dst_bytes[d_idx + 1], dst_bytes[d_idx + 2], dst_bytes[d_idx + 3])
                    out_col = _blend_pixel(src_col, dst_col)
                    dst_bytes[d_idx] = out_col[0]
                    dst_bytes[d_idx + 1] = out_col[1]
                    dst_bytes[d_idx + 2] = out_col[2]
                    dst_bytes[d_idx + 3] = out_col[3]


def _fill_rect(dst: bytearray, dst_w: int, dst_h: int, x: int, y: int, w: int, h: int, color: tuple[int, int, int, int]) -> None:
    for py in range(max(0, y), min(dst_h, y + h)):
        for px in range(max(0, x), min(dst_w, x + w)):
            idx = (py * dst_w + px) * 4
            dst[idx] = color[0]
            dst[idx + 1] = color[1]
            dst[idx + 2] = color[2]
            dst[idx + 3] = color[3]


FONT_5X7 = {
    ' ': ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
    '0': ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    '1': ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    '2': ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    '3': ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    '4': ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    '5': ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    '6': ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    '7': ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    '8': ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    '9': ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    'a': ["00000", "00000", "01110", "00001", "01111", "10001", "01111"],
    'b': ["10000", "10000", "10110", "11001", "10001", "10001", "11110"],
    'c': ["00000", "00000", "01110", "10000", "10000", "10001", "01110"],
    'd': ["00001", "00001", "00111", "01001", "10001", "10001", "01111"],
    'e': ["00000", "00000", "01110", "10001", "11111", "10000", "01110"],
    'f': ["00110", "01001", "01000", "11110", "01000", "01000", "01000"],
    'g': ["00000", "00000", "01111", "10001", "10001", "01111", "00001"],
    'h': ["10000", "10000", "10110", "11001", "10001", "10001", "10001"],
    'i': ["00100", "00000", "01100", "00100", "00100", "00100", "01110"],
    'j': ["00010", "00000", "00010", "00010", "00010", "10010", "01100"],
    'k': ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    'l': ["01100", "00100", "00100", "00100", "00100", "00100", "01110"],
    'm': ["00000", "00000", "11010", "10101", "10101", "10101", "10101"],
    'n': ["00000", "00000", "10110", "11001", "10001", "10001", "10001"],
    'o': ["00000", "00000", "01110", "10001", "10001", "10001", "01110"],
    'p': ["00000", "00000", "11110", "10001", "11110", "10000", "10000"],
    'q': ["00000", "00000", "01111", "10001", "01111", "00001", "00001"],
    'r': ["00000", "00000", "10110", "11001", "10000", "10000", "10000"],
    's': ["00000", "00000", "01110", "10000", "01110", "00001", "11110"],
    't': ["01000", "01000", "11110", "01000", "01000", "01001", "00110"],
    'u': ["00000", "00000", "10001", "10001", "10001", "10011", "01101"],
    'v': ["00000", "00000", "10001", "10001", "01010", "01010", "00100"],
    'w': ["00000", "00000", "10101", "10101", "10101", "10101", "01010"],
    'x': ["00000", "00000", "10001", "01010", "00100", "01010", "10001"],
    'y': ["00000", "00000", "10001", "10001", "01111", "00001", "01110"],
    'z': ["00000", "00000", "11111", "00010", "00100", "01000", "11111"],
    'A': ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    'B': ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    'C': ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    'D': ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    'E': ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    'F': ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    'G': ["01110", "10001", "10000", "10111", "10001", "10001", "01110"],
    'H': ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    'I': ["01110", "00100", "00100", "00100", "00100", "00100", "01110"],
    'J': ["00111", "00010", "00010", "00010", "00010", "10010", "01100"],
    'K': ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    'L': ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    'M': ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    'N': ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    'O': ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    'P': ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    'Q': ["01110", "10001", "10001", "10001", "10101", "10011", "01111"],
    'R': ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    'S': ["01110", "10001", "10000", "01110", "00001", "10001", "01110"],
    'T': ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    'U': ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    'V': ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    'W': ["10001", "10001", "10001", "10101", "10101", "10101", "01010"],
    'X': ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
    'Y': ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    'Z': ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
    '#': ["01010", "01010", "11111", "01010", "11111", "01010", "01010"],
    '-': ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    ':': ["00000", "00100", "00000", "00000", "00100", "00000", "00000"],
    '.': ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    '(': ["00010", "00100", "01000", "01000", "01000", "00100", "00010"],
    ')': ["01000", "00100", "00010", "00010", "00010", "00100", "01000"],
    ',': ["00000", "00000", "00000", "00000", "00100", "00100", "01000"],
}


def _draw_text(dst: bytearray, dst_w: int, dst_h: int, text: str, x: int, y: int, color: tuple[int, int, int, int], scale: int = 1) -> int:
    cur_x = x
    for ch in text:
        bitmap = FONT_5X7.get(ch, FONT_5X7[' '])
        for row_i in range(7):
            row_str = bitmap[row_i]
            for col_i in range(5):
                if row_str[col_i] == '1':
                    for dy in range(scale):
                        py = y + row_i * scale + dy
                        if not (0 <= py < dst_h):
                            continue
                        for dx in range(scale):
                            px = cur_x + col_i * scale + dx
                            if not (0 <= px < dst_w):
                                continue
                            idx = (py * dst_w + px) * 4
                            dst[idx] = color[0]
                            dst[idx + 1] = color[1]
                            dst[idx + 2] = color[2]
                            dst[idx + 3] = color[3]
        cur_x += 6 * scale
    return cur_x


def make_contact_sheet(icons: dict[int, bytes]) -> bytes:
    """Build the official contact sheet matching the icon specs."""
    sheet_w = 1420
    row_h = 280
    mag_h = 320
    header_h = 70
    total_h = header_h + row_h * 4 + mag_h + 50
    sheet = bytearray(sheet_w * total_h * 4)

    # Base background: deep dark canvas (#141412)
    _fill_rect(sheet, sheet_w, total_h, 0, 0, sheet_w, total_h, (20, 20, 18, 255))

    # Title
    _draw_text(sheet, sheet_w, total_h, "Venv Discover - Icon Contact Sheet (1x on 4 backgrounds + 8x zoom)", 24, 20, C_AMBER, scale=2)

    bgs = [
        ("Dark taskbar #202020", BG_DARK_TASKBAR, (220, 220, 220, 255)),
        ("Light taskbar #f3f3f3", BG_LIGHT_TASKBAR, (40, 40, 40, 255)),
        ("App dark #11110f", BG_APP_DARK, (220, 220, 220, 255)),
        ("App light #f4f1e8", BG_APP_LIGHT, (40, 40, 40, 255)),
    ]

    # Calculate X offsets for the 14 sizes
    x_positions = {}
    cur_x = 220
    for s in SIZES:
        x_positions[s] = cur_x
        # Column header text
        col_lbl = f"{s}px"
        _draw_text(sheet, sheet_w, total_h, col_lbl, cur_x, header_h - 16, (140, 140, 130, 255), scale=1)
        cur_x += max(s, 28) + 16

    # Draw the 4 background strips
    for r_idx, (bg_label, bg_col, text_col) in enumerate(bgs):
        y_top = header_h + r_idx * row_h
        # Draw background strip (card)
        _fill_rect(sheet, sheet_w, total_h, 24, y_top + 6, sheet_w - 48, row_h - 12, bg_col)

        # Label on left
        _draw_text(sheet, sheet_w, total_h, bg_label, 36, y_top + 24, text_col, scale=1)

        # Place each icon centered vertically in this row
        for s in SIZES:
            icon_rgba = icons[s]
            x = x_positions[s]
            y = y_top + (row_h - s) // 2
            _blit(sheet, sheet_w, total_h, icon_rgba, s, s, x, y, scale=1)

    # Draw 8x magnification section for 16, 24, 32
    y_mag = header_h + 4 * row_h + 16
    box_w = (sheet_w - 64) // 2
    # Left box: Dark taskbar (#202020)
    _fill_rect(sheet, sheet_w, total_h, 24, y_mag, box_w, mag_h, BG_DARK_TASKBAR)
    _draw_text(sheet, sheet_w, total_h, "8x Hard Pixels: 16px, 24px, 32px (Dark Taskbar #202020)", 36, y_mag + 16, (220, 220, 220, 255), scale=1)

    # Right box: Light taskbar (#f3f3f3)
    _fill_rect(sheet, sheet_w, total_h, 40 + box_w, y_mag, box_w, mag_h, BG_LIGHT_TASKBAR)
    _draw_text(sheet, sheet_w, total_h, "8x Hard Pixels: 16px, 24px, 32px (Light Taskbar #f3f3f3)", 40 + box_w + 16, y_mag + 16, (40, 40, 40, 255), scale=1)

    mag_sizes = [16, 24, 32]
    # Place magnified icons in Dark box
    cur_mag_x = 44
    for s in mag_sizes:
        icon_rgba = icons[s]
        scaled_s = s * 8
        y = y_mag + 40 + (mag_h - 50 - scaled_s) // 2
        _blit(sheet, sheet_w, total_h, icon_rgba, s, s, cur_mag_x, y, scale=8)
        _draw_text(sheet, sheet_w, total_h, f"{s}px (8x)", cur_mag_x + scaled_s // 2 - 16, y + scaled_s + 6, (180, 180, 170, 255), scale=1)
        cur_mag_x += scaled_s + 28

    # Place magnified icons in Light box
    cur_mag_x = 40 + box_w + 24
    for s in mag_sizes:
        icon_rgba = icons[s]
        scaled_s = s * 8
        y = y_mag + 40 + (mag_h - 50 - scaled_s) // 2
        _blit(sheet, sheet_w, total_h, icon_rgba, s, s, cur_mag_x, y, scale=8)
        _draw_text(sheet, sheet_w, total_h, f"{s}px (8x)", cur_mag_x + scaled_s // 2 - 16, y + scaled_s + 6, (60, 60, 60, 255), scale=1)
        cur_mag_x += scaled_s + 28

    return make_png(sheet_w, total_h, bytes(sheet))


def generate_all(repo_root: str | None = None) -> None:
    """Generate all icon assets into their designated locations."""
    if repo_root is None:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    assets_dir = os.path.join(repo_root, "venvdiscover", "assets")
    docs_dir = os.path.join(repo_root, "docs")
    os.makedirs(assets_dir, exist_ok=True)
    os.makedirs(docs_dir, exist_ok=True)

    icons_rgba: dict[int, bytes] = {}
    png_files: dict[int, bytes] = {}

    for s in SIZES:
        rgba = render_icon(s)
        icons_rgba[s] = rgba
        png_files[s] = make_png(s, s, rgba)

    # 1. venvdiscover.ico (14 sizes)
    ico_bytes = make_ico(png_files)
    ico_path = os.path.join(assets_dir, "venvdiscover.ico")
    with open(ico_path, "wb") as f:
        f.write(ico_bytes)

    # 2. venvdiscover-256.png
    png256_path = os.path.join(assets_dir, "venvdiscover-256.png")
    with open(png256_path, "wb") as f:
        f.write(png_files[256])

    # 3. docs/logo-preview.png
    sheet_bytes = make_contact_sheet(icons_rgba)
    sheet_path = os.path.join(docs_dir, "logo-preview.png")
    with open(sheet_path, "wb") as f:
        f.write(sheet_bytes)

    print(f"Generated {ico_path} ({len(ico_bytes)} bytes, {len(SIZES)} sizes)")
    print(f"Generated {png256_path} ({len(png_files[256])} bytes)")
    print(f"Generated {sheet_path} ({len(sheet_bytes)} bytes)")


if __name__ == "__main__":
    generate_all()
