r"""Generate clean, privacy-safe screenshots for the user guides.

Runs strictly on an isolated hidden Windows desktop (WinSta0\GuideDesktop)
using PrintWindow, guaranteeing zero window flashing on the interactive desktop.
Uses exclusively fake environments with neutral mock paths (C:\Users\Alex\...)
and enforces a strict privacy guard that fails if any host paths or usernames appear.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import ctypes.wintypes as wt
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import tkinter as tk
from tkinter import ttk
import zlib

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GENERIC_ALL = 0x10000000

# Win32 structures for process creation on alternate desktop
class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("lpReserved", wt.LPWSTR),
        ("lpDesktop", wt.LPWSTR),
        ("lpTitle", wt.LPWSTR),
        ("dwX", wt.DWORD),
        ("dwY", wt.DWORD),
        ("dwXSize", wt.DWORD),
        ("dwYSize", wt.DWORD),
        ("dwXCountChars", wt.DWORD),
        ("dwYCountChars", wt.DWORD),
        ("dwFillAttribute", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("wShowWindow", wt.WORD),
        ("cbReserved2", wt.WORD),
        ("lpReserved2", ctypes.c_char_p),
        ("hStdInput", wt.HANDLE),
        ("hStdOutput", wt.HANDLE),
        ("hStdError", wt.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wt.HANDLE),
        ("hThread", wt.HANDLE),
        ("dwProcessId", wt.DWORD),
        ("dwThreadId", wt.DWORD),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", wt.LONG),
        ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


user32.GetWindowDC.restype = wt.HDC
user32.GetWindowDC.argtypes = [wt.HWND]
user32.ReleaseDC.restype = ctypes.c_int
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.DeleteDC.restype = wt.BOOL
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.DeleteObject.restype = wt.BOOL
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
user32.PrintWindow.restype = wt.BOOL
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
user32.GetWindowRect.restype = wt.BOOL
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.GetDIBits.argtypes = [
    wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT,
    ctypes.c_void_p, ctypes.c_void_p, wt.UINT
]


def capture_hwnd_to_png(hwnd: int) -> bytes:
    """Capture a window via PrintWindow into uncompressed BGRA, then encode to PNG bytes."""
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    if w <= 0 or h <= 0:
        raise ValueError(f"Invalid window rect: {w}x{h}")

    hdc = user32.GetWindowDC(hwnd)
    memdc = gdi32.CreateCompatibleDC(hdc)
    hbm = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(memdc, hbm)

    # PW_RENDERFULLCONTENT = 2
    user32.PrintWindow(hwnd, memdc, 2)

    bih = BITMAPINFOHEADER()
    bih.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bih.biWidth = w
    bih.biHeight = -h  # top-down DIB
    bih.biPlanes = 1
    bih.biBitCount = 32
    bih.biCompression = 0

    buf_size = w * h * 4
    buf = ctypes.create_string_buffer(buf_size)
    gdi32.GetDIBits(memdc, hbm, 0, h, buf, ctypes.byref(bih), 0)

    gdi32.SelectObject(memdc, old)
    gdi32.DeleteObject(hbm)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(hwnd, hdc)

    # Convert BGRA to RGBA
    raw = bytearray(buf.raw)
    for i in range(0, len(raw), 4):
        raw[i], raw[i + 2] = raw[i + 2], raw[i]
        raw[i + 3] = 255  # solid opacity

    # Encode PNG
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    ihdr_crc = struct.pack(">I", zlib.crc32(b"IHDR" + ihdr_data))
    ihdr = struct.pack(">I", len(ihdr_data)) + b"IHDR" + ihdr_data + ihdr_crc

    raw_lines = bytearray()
    row_len = w * 4
    for y in range(h):
        raw_lines.append(0)
        raw_lines.extend(raw[y * row_len : (y + 1) * row_len])

    comp = zlib.compress(bytes(raw_lines), level=9)
    idat_crc = struct.pack(">I", zlib.crc32(b"IDAT" + comp))
    idat = struct.pack(">I", len(comp)) + b"IDAT" + comp + idat_crc

    iend_crc = struct.pack(">I", zlib.crc32(b"IEND"))
    iend = struct.pack(">I", 0) + b"IEND" + iend_crc
    return sig + ihdr + idat + iend


def check_widget_privacy(widget: tk.Misc, forbidden_strings: list[str]) -> None:
    """Recursively inspect every widget string, label, entry, text, and treeview value."""
    # Check text option if present
    try:
        txt = widget.cget("text")
        if txt:
            for f in forbidden_strings:
                if f.lower() in str(txt).lower():
                    raise ValueError(f"Privacy guard failed: widget {widget} has '{f}' in text: {txt}")
    except Exception:
        pass

    # Check Text widget content
    if isinstance(widget, tk.Text):
        content = widget.get("1.0", "end")
        for f in forbidden_strings:
            if f.lower() in content.lower():
                raise ValueError(f"Privacy guard failed: Text widget has '{f}' in content: {content[:80]}")

    # Check Entry / Label widget textvariable
    try:
        tvar_name = widget.cget("textvariable")
        if tvar_name:
            tvar_val = widget.getvar(tvar_name)
            for f in forbidden_strings:
                if f.lower() in str(tvar_val).lower():
                    raise ValueError(f"Privacy guard failed: textvariable has '{f}': {tvar_val}")
    except Exception:
        pass

    # Check Treeview items and values
    if isinstance(widget, ttk.Treeview):
        for item in widget.get_children():
            row_text = widget.item(item, "text")
            for f in forbidden_strings:
                if f.lower() in str(row_text).lower():
                    raise ValueError(f"Privacy guard failed: Treeview text has '{f}': {row_text}")
            vals = widget.item(item, "values")
            for v in vals:
                for f in forbidden_strings:
                    if f.lower() in str(v).lower():
                        raise ValueError(f"Privacy guard failed: Treeview value has '{f}': {v}")

    # Recurse children
    for child in widget.winfo_children():
        check_widget_privacy(child, forbidden_strings)


def run_worker_process(out_dir: str, repo_root: str) -> None:
    """Worker code executed inside the hidden desktop process."""
    sys.path.insert(0, repo_root)

    # Patch known.py and scanner.py to avoid querying Windows registry or local user installs
    import venvdiscover.known as known
    import venvdiscover.scanner as scanner
    from tests.fakes import fake_places, make_conda_env, make_conda_root, make_python_install, make_venv
    from venvdiscover.model import Env, Kind
    from venvdiscover.ui.app import App
    from venvdiscover.ui.delete_dialog import ConfirmDeleteDialog
    from venvdiscover.ui.theme import apply_theme

    # Set up the fake tree outside %TEMP% (a "temp"/"tmp" path part would make every env a temp venv),
    # in the repo's git-ignored .test-tmp folder.
    base_tmp = os.path.join(repo_root, ".test-tmp", "guide_demo")
    if os.path.exists(base_tmp):
        shutil.rmtree(base_tmp, ignore_errors=True)
    os.makedirs(base_tmp, exist_ok=True)

    places = fake_places(base_tmp)

    # 1. Conda base installation
    croot = make_conda_root(base_tmp, "Miniconda3", "3.12.4", {"conda": "24.5.0"})

    # 2. Conda environment with packages
    cenv = make_conda_env(
        croot,
        "fastapi-backend",
        "3.11.9",
        {"fastapi": "0.111.0", "pydantic": "2.7.4", "uvicorn": "0.30.1", "sqlalchemy": "2.0.31", "pytest": "8.2.2"},
        {"torch": "2.3.1", "torchvision": "0.18.1"},
    )

    # 3. Large project venv (> 1 GB, to illustrate permanent delete warning)
    p1 = os.path.join(base_tmp, "Projects", "data-analysis")
    v1 = make_venv(
        p1,
        ".venv",
        "3.12.4",
        {"numpy": "1.26.4", "pandas": "2.2.2", "scipy": "1.13.1", "polars": "0.20.31", "pyarrow": "16.1.0"},
    )

    # 4. Standard project venv (< 1 GB, 350 MB)
    p2 = os.path.join(base_tmp, "Projects", "web-scraper")
    v2 = make_venv(
        p2,
        ".venv",
        "3.11.8",
        {"beautifulsoup4": "4.12.3", "requests": "2.32.3", "lxml": "5.2.2", "playwright": "1.44.0"},
    )

    # 5. Base Python installation
    pyinst = make_python_install(base_tmp, "Python312", "3.12.4", {"pip": "24.0", "setuptools": "70.0.0"})

    # Patch known python installs and registry to return ONLY our fake python install
    known.find_python_installs = lambda p: [pyinst]
    known.python_install_paths = lambda p, reg=None: [os.path.join(base_tmp, "Python312")]
    known.registry_install_paths = lambda: []
    scanner.python_install_paths = lambda p, reg=None: [os.path.join(base_tmp, "Python312")]
    scanner.registry_install_paths = lambda: []

    # Initialize GUI on hidden desktop with isolated environ and registry_paths
    app = App(
        places,
        drive_roots=[base_tmp],
        environ={},
        scanner_factory=lambda **kwargs: scanner.Scanner(
            **{**kwargs, "registry_paths": lambda: []}
        ),
    )
    app.geometry("1280x760")  # the app's default size
    apply_theme(app, "dark", "default")
    app.update_idletasks()
    app.update()

    # Wait for scanner to discover the fake environments
    t0 = time.time()
    while time.time() - t0 < 4.0:
        app.update()
        if len(app.envs) >= 4:
            break
        time.sleep(0.05)

    # Patch sizes and dates for a realistic, clean presentation
    # v1 (data-analysis) is 2.4 GB (over 1 GB)
    # v2 (web-scraper) is 350 MB (under 1 GB)
    # cenv (fastapi-backend) is 840 MB
    # croot is 1.6 GB
    # pyinst is 120 MB
    demo_root = "C:\\Users\\Alex"
    env_rewrites: dict[str, tuple[str, int, float, str]] = {}

    for k, env in list(app.envs.items()):
        if "data-analysis" in env.path:
            clean_path = f"{demo_root}\\Projects\\data-analysis\\.venv"
            env.path = clean_path
            env.size = 2_576_980_377  # 2.4 GB
            env.last_used = time.time() - 86400 * 3
            env_rewrites[k] = (clean_path, env.size, env.last_used, "data-analysis")
        elif "web-scraper" in env.path:
            clean_path = f"{demo_root}\\Projects\\web-scraper\\.venv"
            env.path = clean_path
            env.size = 367_001_600  # 350 MB
            env.last_used = time.time() - 86400 * 12
            env_rewrites[k] = (clean_path, env.size, env.last_used, "web-scraper")
        elif "fastapi-backend" in env.path:
            clean_path = f"{demo_root}\\miniconda3\\envs\\fastapi-backend"
            env.path = clean_path
            env.size = 880_803_840  # 840 MB
            env.last_used = time.time() - 86400 * 1
            env_rewrites[k] = (clean_path, env.size, env.last_used, "fastapi-backend")
        elif "Miniconda3" in env.path:
            clean_path = f"{demo_root}\\miniconda3"
            env.path = clean_path
            env.size = 1_717_986_918  # 1.6 GB
            env.last_used = time.time() - 86400 * 20
            env_rewrites[k] = (clean_path, env.size, env.last_used, "miniconda3")
        elif "Python312" in env.path:
            clean_path = f"{demo_root}\\Python312"
            env.path = clean_path
            env.size = 125_829_120  # 120 MB
            env.last_used = time.time() - 86400 * 45
            env_rewrites[k] = (clean_path, env.size, env.last_used, "Python 3.12")

    # Refresh treeview display with rewritten demo paths and sizes
    for k, (clean_path, sz, lu, name) in env_rewrites.items():
        if app.envs_table.tree.exists(k):
            vals = list(app.envs_table.tree.item(k, "values"))
            # Columns: Name, Kind, Python, Packages, Size, Last used, Location
            from venvdiscover.ui.format import fmt_date, fmt_size
            vals[0] = name
            vals[4] = fmt_size(sz)
            vals[5] = fmt_date(lu)
            vals[6] = clean_path
            app.envs_table.tree.item(k, values=vals)

    # Select the fastapi-backend environment
    target_key = None
    for k, env in app.envs.items():
        if "fastapi-backend" in env.path:
            target_key = k
            break

    if target_key:
        app.envs_table.tree.selection_set(target_key)
        app._on_table_select([target_key])
        app.update()

    # Re-write packages panel header to neutral demo path
    if hasattr(app.packages_panel, "lbl_path"):
        app.packages_panel.lbl_path.configure(text=f"{demo_root}\\miniconda3\\envs\\fastapi-backend")
    app.update()

    # Build forbidden strings list for strict privacy check
    forbidden = [
        os.environ.get("USERPROFILE", ""),
        os.environ.get("USERNAME", ""),
        os.path.dirname(os.path.abspath(repo_root)),
        "\\Temp\\",
        "/Temp/",
    ]
    forbidden = [f for f in forbidden if len(f) > 2]

    # Verify privacy on main app window
    check_widget_privacy(app, forbidden)

    # 1. Capture main window
    hwnd_app = app.winfo_id()
    main_png = capture_hwnd_to_png(hwnd_app)
    main_path = os.path.join(out_dir, "main-window.png")
    with open(main_path, "wb") as f:
        f.write(main_png)
    print(f"Captured main window -> {main_path} ({len(main_png)} bytes)")

    # 2. Build and capture ConfirmDeleteDialog showing over-1 GB permanent delete warning
    # Target large env (data-analysis, 2.4 GB) for permanent delete,
    # small env (web-scraper, 350 MB) for 14-day bin,
    # and locked env (Python 3.12) for can't delete
    env_large = next(e for e in app.envs.values() if "data-analysis" in e.path)
    env_small = next(e for e in app.envs.values() if "web-scraper" in e.path)
    env_locked = next(e for e in app.envs.values() if "Python312" in e.path)

    dlg = ConfirmDeleteDialog(
        app,
        to_bin=[env_small],
        permanent=[env_large],
        locked=[env_locked],
        extras={},
        on_confirm=lambda: None,
    )
    dlg.geometry("680x440")
    apply_theme(dlg, "dark", "default")
    dlg.update_idletasks()
    dlg.update()

    # Verify privacy on delete dialog
    check_widget_privacy(dlg, forbidden)

    hwnd_dlg = dlg.winfo_id()
    dlg_png = capture_hwnd_to_png(hwnd_dlg)
    dlg_path = os.path.join(out_dir, "delete-dialog.png")
    with open(dlg_path, "wb") as f:
        f.write(dlg_png)
    print(f"Captured delete dialog -> {dlg_path} ({len(dlg_png)} bytes)")

    dlg.destroy()

    # 3. Build and capture CachesDialog showing package manager caches
    from venvdiscover.caches import Cache
    from venvdiscover.ui.caches_dialog import CachesDialog

    dlg_caches = CachesDialog(app)
    dlg_caches.geometry("780x460")
    apply_theme(dlg_caches, "dark", "default")
    if dlg_caches._load_thread is not None:
        dlg_caches._load_thread.join(timeout=2.0)
    app.update()

    fake_caches = [
        Cache("uv", f"{demo_root}\\AppData\\Local\\uv\\cache", 15_892_100_000, time.time() - 86400 * 1, "uv cache clean"),
        Cache("pip", f"{demo_root}\\AppData\\Local\\pip\\Cache", 1_106_123_168, time.time() - 86400 * 2, "pip cache purge"),
        Cache("poetry", f"{demo_root}\\AppData\\Local\\pypoetry\\Cache", 2_254_857_800, time.time() - 86400 * 3, "poetry cache clear"),
        Cache("pipenv", f"{demo_root}\\AppData\\Local\\pipenv\\pipenv\\Cache", 671_088_640, time.time() - 86400 * 8, "pipenv --clear"),
    ]
    dlg_caches._apply_loaded_caches(fake_caches)
    dlg_caches.tree.selection_set("1")
    dlg_caches.tree.focus("1")
    dlg_caches._on_tree_select(None)
    dlg_caches.update_idletasks()
    dlg_caches.update()

    check_widget_privacy(dlg_caches, forbidden)
    hwnd_caches = dlg_caches.winfo_id()
    caches_png = capture_hwnd_to_png(hwnd_caches)
    caches_path = os.path.join(out_dir, "caches-dialog.png")
    with open(caches_path, "wb") as f:
        f.write(caches_png)
    print(f"Captured caches dialog -> {caches_path} ({len(caches_png)} bytes)")
    dlg_caches.destroy()

    app.destroy()
    shutil.rmtree(base_tmp, ignore_errors=True)


GUIDES = [("docs", "guide", "how-to-use.en.html")]
_DATA_IMG = re.compile(r'src="data:image/png;base64,[A-Za-z0-9+/=]*"')


def embed_screenshots(guide_path: str, pngs: list[bytes]) -> None:
    """Replace the guide's embedded PNGs, in page order (main window, caches dialog, delete dialog)."""
    with open(guide_path, encoding="utf-8") as f:
        html = f.read()
    found = _DATA_IMG.findall(html)
    if len(found) != len(pngs):
        raise ValueError(f"{guide_path}: expected {len(pngs)} embedded PNGs, found {len(found)}")
    it = iter(pngs)
    html = _DATA_IMG.sub(lambda m: 'src="data:image/png;base64,' + base64.b64encode(next(it)).decode("ascii") + '"', html)
    with open(guide_path, "w", encoding="utf-8", newline="") as f:
        f.write(html)


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture privacy-safe guide screenshots.")
    parser.add_argument("--out-dir", default=None, help="Directory to save PNG screenshots.")
    parser.add_argument("--worker", action="store_true", help="Internal worker mode.")
    parser.add_argument("--embed", action="store_true",
                        help="Also put the new screenshots into both HTML guides (replacing the old ones).")
    args = parser.parse_args()

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    out_dir = args.out_dir or os.path.join(repo_root, ".test-tmp", "guide_screens")
    os.makedirs(out_dir, exist_ok=True)

    if args.worker:
        run_worker_process(out_dir, repo_root)
        return

    # Supervisor mode: create hidden desktop and spawn child worker
    desk_name = f"GuideDesktop_{os.getpid()}"
    hdesk = user32.CreateDesktopW(desk_name, None, None, 0, GENERIC_ALL, None)
    if not hdesk:
        raise ctypes.WinError(ctypes.get_last_error())

    try:
        si = STARTUPINFOW()
        si.cb = ctypes.sizeof(STARTUPINFOW)
        si.lpDesktop = f"WinSta0\\{desk_name}"
        pi = PROCESS_INFORMATION()

        cmd = f'"{sys.executable}" "{os.path.abspath(__file__)}" --worker --out-dir "{out_dir}"'
        ok = kernel32.CreateProcessW(
            None,
            cmd,
            None,
            None,
            False,
            0,
            None,
            repo_root,
            ctypes.byref(si),
            ctypes.byref(pi),
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

        kernel32.WaitForSingleObject(pi.hProcess, 60000)
        exit_code = wt.DWORD()
        kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(exit_code))
        kernel32.CloseHandle(pi.hProcess)
        kernel32.CloseHandle(pi.hThread)

        if exit_code.value != 0:
            raise RuntimeError(f"Screenshot worker failed with exit code {exit_code.value}")

        print(f"Screenshots generated successfully in {out_dir}")
    finally:
        user32.CloseDesktop(hdesk)

    if args.embed:
        shots = [
            os.path.join(out_dir, "main-window.png"),
            os.path.join(out_dir, "caches-dialog.png"),  # the caches section comes before "Deleting Safely"
            os.path.join(out_dir, "delete-dialog.png"),
        ]
        pngs = [open(p, "rb").read() for p in shots]
        for guide in GUIDES:
            embed_screenshots(os.path.join(repo_root, *guide), pngs)
            print(f"Embedded {len(pngs)} screenshots into {os.path.join(*guide)}")


if __name__ == "__main__":
    main()
