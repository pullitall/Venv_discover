"""Build the shareable release: dist/VenvDiscover-<version>.zip.

Standard library only; run it with the pyenv Python 3.11 from a clean checkout of the release commit:

    python scripts/build_release.py [--skip-tests]

Steps:
1. the full test suite (scripts/run_tests.py, on a hidden desktop);
2. `.build-venv` (git-ignored) with PyInstaller pinned below; nothing is installed anywhere else;
3. VenvDiscover.exe: one file, no console, the app icon, the assets and a Windows version resource;
4. smoke test: the exe starts on a hidden desktop inside a kill-on-close job, with throwaway app-data
   folders; its "Venv Discover" window must appear with the app's amber icon, then the job closes it;
5. the zip: the exe, the user guide, the source (git archive of HEAD without private notes) and
   SHA256SUMS.txt. The build stops if the source mentions this PC's user profile or name.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import hashlib
import io
import json
import msvcrt
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

from venvdiscover import __version__


def parse_file_version(ver: str) -> tuple[int, int, int, int]:
    """Parse a dot-separated version string into a 4-integer tuple for Windows version resource."""
    parts = [int(x) for x in ver.split(".")]
    return tuple((parts + [0, 0, 0, 0])[:4])  # type: ignore[return-value]


VERSION = __version__
FILE_VERSION = parse_file_version(__version__)
PYINSTALLER = "pyinstaller==6.22.3"  # released 2026-09-12
APP_NAME = "VenvDiscover"
WINDOW_TITLE = "Venv Discover"
# Private notes may describe this PC; they can stay in a checkout but never go into the shared source.
EXCLUDE_FROM_SOURCE = ("docs/design.md", "docs/notes/")
PACKAGE_DOCS = {
    "docs/guide/how-to-use.en.html": "How to use (EN).html",
}
TEXT_EXTENSIONS = (".py", ".pyw", ".md", ".html", ".json", ".txt", ".ps1", ".cfg", ".toml", ".gitignore")


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO_ROOT, text=True).strip()


def is_excluded(path: str) -> bool:
    """True for the files left out of the shared source."""
    p = path.replace("\\", "/")
    return any(p == e or (e.endswith("/") and p.startswith(e)) for e in EXCLUDE_FROM_SOURCE)


def private_markers(environ: dict[str, str] | None = None) -> list[str]:
    """Strings that must not appear in the shared source: this PC's user profile folder and user name."""
    env = os.environ if environ is None else environ
    marks = [env.get("USERPROFILE", ""), env.get("USERNAME", "")]
    return [m for m in marks if len(m) > 2]


def find_private_mentions(root: str, markers: list[str]) -> list[tuple[str, str]]:
    """(relative path, marker) for every text file under root that contains a marker (case-insensitive)."""
    hits: list[tuple[str, str]] = []
    lowered = [m.lower() for m in markers]
    for dirpath, _dirs, files in os.walk(root):
        for name in sorted(files):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace("\\", "/")
            if not name.lower().endswith(TEXT_EXTENSIONS):
                continue
            with open(full, encoding="utf-8", errors="replace") as f:
                text = f.read().lower()
            for raw, low in zip(markers, lowered):
                if low in text:
                    hits.append((rel, raw))
    return sorted(hits)


def version_file_text(version: str, file_version: tuple[int, int, int, int], name: str = APP_NAME, desc: str | None = None) -> str:
    """PyInstaller --version-file contents (a VSVersionInfo literal)."""
    fv = ".".join(str(n) for n in file_version)
    if desc is None:
        desc = "Venv Discover - find, inspect and safely clean up Python environments"
    strings = [
        ("CompanyName", ""),
        ("FileDescription", desc),
        ("FileVersion", fv),
        ("InternalName", name),
        ("LegalCopyright", "Copyright (c) 2026 pullitall. MIT License."),
        ("OriginalFilename", f"{name}.exe"),
        ("ProductName", WINDOW_TITLE),
        ("ProductVersion", version),
    ]
    table = ",\n".join(f"          StringStruct({k!r}, {v!r})" for k, v in strings)
    return (
        "VSVersionInfo(\n"
        f"  ffi=FixedFileInfo(filevers={file_version}, prodvers={file_version}, mask=0x3f, flags=0x0,\n"
        "                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),\n"
        "  kids=[\n"
        "    StringFileInfo([\n"
        "      StringTable('040904B0', [\n"
        f"{table}\n"
        "      ])\n"
        "    ]),\n"
        "    VarFileInfo([VarStruct('Translation', [1033, 1200])])\n"
        "  ]\n"
        ")\n"
    )


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_sums(package_dir: str) -> str:
    """SHA256SUMS.txt for every file in the package (sorted, forward slashes)."""
    lines = []
    for dirpath, _dirs, files in os.walk(package_dir):
        for name in files:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, package_dir).replace("\\", "/")
            if rel != "SHA256SUMS.txt":
                lines.append(f"{sha256_of(full)}  {rel}")
    path = os.path.join(package_dir, "SHA256SUMS.txt")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(sorted(lines, key=lambda s: s.split("  ", 1)[1])) + "\n")
    return path


def zip_dir(src_dir: str, zip_path: str, root_name: str, date_time: tuple[int, int, int, int, int, int]) -> None:
    """Zip src_dir under root_name/, with sorted entries and one fixed timestamp (same input, same bytes)."""
    entries = []
    for dirpath, _dirs, files in os.walk(src_dir):
        for name in files:
            full = os.path.join(dirpath, name)
            entries.append((os.path.relpath(full, src_dir).replace("\\", "/"), full))
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel, full in sorted(entries):
            info = zipfile.ZipInfo(f"{root_name}/{rel}", date_time=date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(full, "rb") as f:
                z.writestr(info, f.read(), compresslevel=9)


def export_source(dest: str) -> list[str]:
    """git archive of HEAD into dest, without the excluded files. Returns the exported paths."""
    data = subprocess.check_output(["git", "archive", "--format=tar", "HEAD"], cwd=REPO_ROOT)
    kept = []
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for m in tar.getmembers():
            if m.isfile() and not is_excluded(m.name):
                if hasattr(tarfile, "data_filter"):  # Python 3.11.4+
                    tar.extract(m, dest, filter="data")
                else:
                    tar.extract(m, dest)
                kept.append(m.name)
    return sorted(kept)


# --- build steps ---------------------------------------------------------------------------------------------

def run_test_suite() -> None:
    log("running the full test suite on a hidden desktop")
    r = subprocess.run([sys.executable, os.path.join(REPO_ROOT, "scripts", "run_tests.py"), "--workers", "3"],
                       cwd=REPO_ROOT)
    if r.returncode != 0:
        raise SystemExit("tests failed: no release built")


def ensure_build_venv() -> str:
    venv = os.path.join(REPO_ROOT, ".build-venv")
    py = os.path.join(venv, "Scripts", "python.exe")
    if not os.path.isfile(py):
        log(f"creating {venv}")
        subprocess.check_call([sys.executable, "-m", "venv", venv])
    want = PYINSTALLER.split("==")[1]
    have = subprocess.run([py, "-c", "import PyInstaller; print(PyInstaller.__version__)"],
                          capture_output=True, text=True).stdout.strip()
    if have != want:
        log(f"installing {PYINSTALLER} into .build-venv")
        subprocess.check_call([py, "-m", "pip", "install", "--disable-pip-version-check", "-q", PYINSTALLER])
    return py


def build_exe(build_py: str, work: str) -> str:
    version_file = os.path.join(work, "version.txt")
    with open(version_file, "w", encoding="utf-8") as f:
        f.write(version_file_text(VERSION, FILE_VERSION))
    assets = os.path.join(REPO_ROOT, "venvdiscover", "assets")
    dist = os.path.join(work, "dist")
    cmd = [
        build_py, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", APP_NAME,
        "--icon", os.path.join(assets, "venvdiscover.ico"),
        "--add-data", f"{assets};venvdiscover/assets",
        "--version-file", version_file,
        "--paths", REPO_ROOT,
        "--distpath", dist, "--workpath", os.path.join(work, "pyi"), "--specpath", work,
        os.path.join(REPO_ROOT, "VenvDiscover.pyw"),
    ]
    log("building the exe with PyInstaller")
    with open(os.path.join(work, "pyinstaller.log"), "w", encoding="utf-8") as logf:
        r = subprocess.run(cmd, cwd=REPO_ROOT, stdout=logf, stderr=subprocess.STDOUT)
    exe = os.path.join(dist, f"{APP_NAME}.exe")
    if r.returncode != 0 or not os.path.isfile(exe):
        raise SystemExit(f"PyInstaller failed (see {os.path.join(work, 'pyinstaller.log')})")
    return exe



def _amber_share_of_class_icon(hwnd: int) -> float:
    """Share of amber pixels in the window's class icon drawn at 32 px (the app's logo is amber, Tk's feather isn't)."""
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.GetClassLongPtrW.restype = ctypes.c_size_t
    user32.GetClassLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
    hicon = user32.GetClassLongPtrW(hwnd, -14) or user32.GetClassLongPtrW(hwnd, -34)  # GCLP_HICON / HICONSM
    if not hicon:
        return 0.0
    user32.GetDC.restype = wt.HDC
    user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
    user32.DrawIconEx.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_size_t, ctypes.c_int, ctypes.c_int,
                                  wt.UINT, wt.HANDLE, wt.UINT]
    gdi32.CreateCompatibleDC.restype = wt.HDC
    gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
    gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
    gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = wt.HGDIOBJ
    gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
    gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
    gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wt.HDC]
    size = 32
    sdc = user32.GetDC(None)
    mdc = gdi32.CreateCompatibleDC(sdc)
    bmp = gdi32.CreateCompatibleBitmap(sdc, size, size)
    old = gdi32.SelectObject(mdc, bmp)
    user32.DrawIconEx(mdc, 0, 0, hicon, size, size, 0, None, 3)  # DI_NORMAL
    header = ctypes.create_string_buffer(struct.pack("<IiiHHIIiiII", 40, size, -size, 1, 32, 0, 0, 0, 0, 0, 0), 44)
    pixels = ctypes.create_string_buffer(size * size * 4)
    gdi32.GetDIBits(mdc, bmp, 0, size, pixels, header, 0)
    gdi32.SelectObject(mdc, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mdc)
    user32.ReleaseDC(None, sdc)
    raw = pixels.raw
    amber = sum(1 for i in range(0, len(raw), 4) if raw[i + 2] > 200 and 120 < raw[i + 1] < 220 and raw[i] < 110)
    return amber / (size * size)


def smoke_test(exe: str, timeout: float = 30.0) -> dict:
    """Start the exe on a hidden desktop in a kill-on-close job; wait for its window; check the icon; close it."""
    import run_tests as rt  # the hidden-desktop helpers (scripts/run_tests.py)

    fake = tempfile.mkdtemp(prefix="vd-release-smoke-")
    env = dict(os.environ)
    for key, sub in (("LOCALAPPDATA", "local"), ("APPDATA", "roaming"), ("USERPROFILE", "home")):
        env[key] = os.path.join(fake, sub)
        os.makedirs(env[key], exist_ok=True)
    block = ctypes.create_unicode_buffer("".join(f"{k}={v}\0" for k, v in env.items()) + "\0")

    desk = f"vdrelease-{os.getpid()}"
    hdesk = rt.create_hidden_desktop(desk)
    if not hdesk:
        raise SystemExit("smoke test: could not create a hidden desktop")
    job = rt.create_job_object()
    si = rt.STARTUPINFOW()
    si.cb = ctypes.sizeof(si)
    si.lpDesktop = f"WinSta0\\{desk}"
    pi = rt.PROCESS_INFORMATION()
    result = {"window_after_s": None, "amber_share": 0.0, "owner_desktop_windows": 0}
    t0 = time.perf_counter()
    try:
        ok = rt.kernel32.CreateProcessW(None, ctypes.create_unicode_buffer(f'"{exe}"'), None, None, False,
                                        0x4 | 0x400 | 0x08000000,  # SUSPENDED | UNICODE_ENVIRONMENT | NO_WINDOW
                                        block, os.path.dirname(exe), ctypes.byref(si), ctypes.byref(pi))
        if not ok:
            raise SystemExit(f"smoke test: CreateProcessW failed ({ctypes.get_last_error()})")
        rt.kernel32.AssignProcessToJobObject(job, pi.hProcess)
        rt.kernel32.ResumeThread(pi.hThread)
        hwnd = None
        while time.perf_counter() - t0 < timeout and hwnd is None:
            pids = rt.get_job_process_ids(job) | {int(pi.dwProcessId)}
            result["owner_desktop_windows"] = max(result["owner_desktop_windows"],
                                                  len(rt.windows_on_owner_desktop(pids)))
            for h in rt.windows_on_hidden_desktop(hdesk, pids):
                if rt.get_window_title(h) == WINDOW_TITLE and rt.user32.IsWindowVisible(h):
                    hwnd = h
                    result["window_after_s"] = round(time.perf_counter() - t0, 2)
                    break
            else:
                time.sleep(0.05)
        if hwnd is not None:
            time.sleep(2.0)  # let it finish drawing and settle
            result["amber_share"] = round(_amber_share_of_class_icon(hwnd), 3)
    finally:
        rt.kernel32.CloseHandle(job)  # kills the exe and its unpacked child
        rt.kernel32.CloseHandle(pi.hThread)
        rt.kernel32.CloseHandle(pi.hProcess)
        time.sleep(0.5)
        rt.close_hidden_desktop(hdesk)
        shutil.rmtree(fake, ignore_errors=True)
    if result["window_after_s"] is None:
        raise SystemExit(f"smoke test: no '{WINDOW_TITLE}' window within {timeout:.0f} s")
    if result["owner_desktop_windows"]:
        raise SystemExit("smoke test: the exe showed a window on the owner's desktop")
    if result["amber_share"] < 0.10:
        raise SystemExit(f"smoke test: the window icon isn't the app's logo (amber share {result['amber_share']})")
    return result


def smoke_test_cli(exe: str, timeout: float = 30.0) -> dict:
    """Smoke test VenvDiscover.exe in CLI mode on a hidden desktop in a job:
    1. with captured handles: check exit code 0, valid schema JSON, and no window;
    2. with no inherited handles: check exit code 4, no window, and no error dialog.
    """
    import run_tests as rt

    fake = tempfile.mkdtemp(prefix="vd-cli-smoke-")
    env = dict(os.environ)
    for key, sub in (("LOCALAPPDATA", "local"), ("APPDATA", "roaming"), ("USERPROFILE", "home")):
        env[key] = os.path.join(fake, sub)
        os.makedirs(env[key], exist_ok=True)
    block = ctypes.create_unicode_buffer("".join(f"{k}={v}\0" for k, v in env.items()) + "\0")

    result = {
        "captured_exit": None,
        "json_valid": False,
        "no_handles_exit": None,
        "owner_desktop_windows": 0,
        "hidden_desktop_windows": 0,
    }

    # Run 1: with captured output
    out_path = os.path.join(fake, "stdout.json")
    err_path = os.path.join(fake, "stderr.log")
    out_f = open(out_path, "wb")
    err_f = open(err_path, "wb")
    out_h = msvcrt.get_osfhandle(out_f.fileno())
    err_h = msvcrt.get_osfhandle(err_f.fileno())
    os.set_handle_inheritable(out_h, True)
    os.set_handle_inheritable(err_h, True)

    desk1 = f"vdrelease-cli1-{os.getpid()}"
    hdesk1 = rt.create_hidden_desktop(desk1)
    if not hdesk1:
        shutil.rmtree(fake, ignore_errors=True)
        raise SystemExit("smoke test cli: could not create a hidden desktop")
    job1 = rt.create_job_object()
    si1 = rt.STARTUPINFOW()
    si1.cb = ctypes.sizeof(si1)
    si1.lpDesktop = f"WinSta0\\{desk1}"
    si1.dwFlags = rt.STARTF_USESTDHANDLES
    si1.hStdOutput = out_h
    si1.hStdError = err_h
    pi1 = rt.PROCESS_INFORMATION()

    t0 = time.perf_counter()
    try:
        cmdline = f'"{exe}" --json --known-only'
        ok = rt.kernel32.CreateProcessW(
            None, ctypes.create_unicode_buffer(cmdline), None, None, True,
            0x4 | 0x400 | 0x08000000,  # SUSPENDED | UNICODE_ENVIRONMENT | NO_WINDOW
            block, os.path.dirname(exe), ctypes.byref(si1), ctypes.byref(pi1),
        )
        if not ok:
            raise SystemExit(f"smoke test cli: CreateProcessW failed ({ctypes.get_last_error()})")
        rt.kernel32.AssignProcessToJobObject(job1, pi1.hProcess)
        rt.kernel32.ResumeThread(pi1.hThread)

        while time.perf_counter() - t0 < timeout:
            pids = rt.get_job_process_ids(job1) | {int(pi1.dwProcessId)}
            result["owner_desktop_windows"] = max(
                result["owner_desktop_windows"], len(rt.windows_on_owner_desktop(pids))
            )
            visible_hidden = [h for h in rt.windows_on_hidden_desktop(hdesk1, pids) if rt.user32.IsWindowVisible(h)]
            result["hidden_desktop_windows"] = max(
                result["hidden_desktop_windows"], len(visible_hidden)
            )
            exit_code = wt.DWORD()
            if rt.kernel32.GetExitCodeProcess(pi1.hProcess, ctypes.byref(exit_code)):
                if exit_code.value != 259:  # STILL_ACTIVE
                    result["captured_exit"] = exit_code.value
                    break
            time.sleep(0.05)
    finally:
        rt.kernel32.CloseHandle(job1)
        rt.kernel32.CloseHandle(pi1.hThread)
        rt.kernel32.CloseHandle(pi1.hProcess)
        out_f.close()
        err_f.close()
        time.sleep(0.5)
        rt.close_hidden_desktop(hdesk1)

    if result["owner_desktop_windows"]:
        shutil.rmtree(fake, ignore_errors=True)
        raise SystemExit("smoke test cli: showed a window on the owner's desktop")
    if result["hidden_desktop_windows"]:
        shutil.rmtree(fake, ignore_errors=True)
        raise SystemExit("smoke test cli: showed a visible window or dialog on the hidden desktop")
    if result["captured_exit"] != 0:
        err_msg = ""
        if os.path.isfile(err_path):
            with open(err_path, "r", encoding="utf-8", errors="replace") as f:
                err_msg = f.read()
        shutil.rmtree(fake, ignore_errors=True)
        raise SystemExit(f"smoke test cli: exited with code {result['captured_exit']} (stderr: {err_msg})")

    try:
        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("schema") == 1 and data.get("app") == VERSION and isinstance(data.get("envs"), list):
            result["json_valid"] = True
    except Exception as exc:
        shutil.rmtree(fake, ignore_errors=True)
        raise SystemExit(f"smoke test cli: stdout was not valid schema JSON: {exc}")

    # Run 2: with no inherited handles (stdout=None, stderr=None) -> must exit 4 with no window/dialog
    desk2 = f"vdrelease-cli2-{os.getpid()}"
    hdesk2 = rt.create_hidden_desktop(desk2)
    if not hdesk2:
        shutil.rmtree(fake, ignore_errors=True)
        raise SystemExit("smoke test cli (no handles): could not create hidden desktop")
    job2 = rt.create_job_object()
    si2 = rt.STARTUPINFOW()
    si2.cb = ctypes.sizeof(si2)
    si2.lpDesktop = f"WinSta0\\{desk2}"
    pi2 = rt.PROCESS_INFORMATION()

    t0 = time.perf_counter()
    try:
        cmdline = f'"{exe}" --json --known-only'
        ok = rt.kernel32.CreateProcessW(
            None, ctypes.create_unicode_buffer(cmdline), None, None, False,
            0x4 | 0x400 | 0x08000000,  # SUSPENDED | UNICODE_ENVIRONMENT | NO_WINDOW
            block, os.path.dirname(exe), ctypes.byref(si2), ctypes.byref(pi2),
        )
        if not ok:
            raise SystemExit(f"smoke test cli (no handles): CreateProcessW failed ({ctypes.get_last_error()})")
        rt.kernel32.AssignProcessToJobObject(job2, pi2.hProcess)
        rt.kernel32.ResumeThread(pi2.hThread)

        while time.perf_counter() - t0 < timeout:
            pids = rt.get_job_process_ids(job2) | {int(pi2.dwProcessId)}
            result["owner_desktop_windows"] = max(
                result["owner_desktop_windows"], len(rt.windows_on_owner_desktop(pids))
            )
            visible_hidden2 = [h for h in rt.windows_on_hidden_desktop(hdesk2, pids) if rt.user32.IsWindowVisible(h)]
            result["hidden_desktop_windows"] = max(
                result["hidden_desktop_windows"], len(visible_hidden2)
            )
            exit_code = wt.DWORD()
            if rt.kernel32.GetExitCodeProcess(pi2.hProcess, ctypes.byref(exit_code)):
                if exit_code.value != 259:  # STILL_ACTIVE
                    result["no_handles_exit"] = exit_code.value
                    break
            time.sleep(0.05)
    finally:
        rt.kernel32.CloseHandle(job2)
        rt.kernel32.CloseHandle(pi2.hThread)
        rt.kernel32.CloseHandle(pi2.hProcess)
        time.sleep(0.5)
        rt.close_hidden_desktop(hdesk2)
        shutil.rmtree(fake, ignore_errors=True)

    if result["owner_desktop_windows"]:
        raise SystemExit("smoke test cli (no handles): showed a window on owner desktop")
    if result["hidden_desktop_windows"]:
        raise SystemExit("smoke test cli (no handles): showed a visible window or error dialog on hidden desktop")
    if result["no_handles_exit"] != 4:
        raise SystemExit(f"smoke test cli (no handles): expected exit code 4, got {result['no_handles_exit']}")

    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build dist/VenvDiscover-<version>.zip")
    ap.add_argument("--skip-tests", action="store_true", help="skip the test suite (for a quick rebuild)")
    ap.add_argument("--allow-dirty", action="store_true", help="build even with uncommitted changes")
    args = ap.parse_args(argv)

    if git("status", "--porcelain") and not args.allow_dirty:
        raise SystemExit("uncommitted changes: commit first (the source in the zip comes from HEAD)")
    commit = git("rev-parse", "--short", "HEAD")
    stamp = time.strptime(git("log", "-1", "--format=%cd", "--date=format:%Y-%m-%d %H:%M:%S"), "%Y-%m-%d %H:%M:%S")
    date_time = tuple(stamp[:6])
    log(f"release {VERSION} from commit {commit}")

    if not args.skip_tests:
        run_test_suite()

    build_root = os.path.join(REPO_ROOT, "build", "release")
    shutil.rmtree(build_root, ignore_errors=True)
    os.makedirs(build_root)
    package_name = f"{APP_NAME}-{VERSION}"
    package = os.path.join(build_root, package_name)
    os.makedirs(package)

    log("exporting the source (without private notes)")
    source_dir = os.path.join(package, "source")
    exported = export_source(source_dir)
    mentions = find_private_mentions(source_dir, private_markers())
    if mentions:
        listing = "\n".join(f"  {path}: {mark}" for path, mark in mentions)
        raise SystemExit(f"the source mentions this PC's user profile or name:\n{listing}")

    exe = build_exe(ensure_build_venv(), build_root)
    log("smoke test on a hidden desktop (GUI mode)")
    smoke = smoke_test(exe)
    log(f"  window after {smoke['window_after_s']} s, icon amber share {smoke['amber_share']}, "
        f"windows on the owner's desktop {smoke['owner_desktop_windows']}")

    log("smoke test on a hidden desktop (CLI mode)")
    smoke_cli = smoke_test_cli(exe)
    log(f"  captured exit {smoke_cli['captured_exit']}, json valid {smoke_cli['json_valid']}, "
        f"no-handles exit {smoke_cli['no_handles_exit']}, owner desktop windows {smoke_cli['owner_desktop_windows']}")
    shutil.copy2(exe, os.path.join(package, f"{APP_NAME}.exe"))

    for src, name in PACKAGE_DOCS.items():
        shutil.copy2(os.path.join(source_dir, *src.split("/")), os.path.join(package, name))
    write_sums(package)
    dist = os.path.join(REPO_ROOT, "dist")
    os.makedirs(dist, exist_ok=True)
    zip_path = os.path.join(dist, f"{package_name}.zip")
    zip_dir(package, zip_path, package_name, date_time)  # type: ignore[arg-type]

    log(f"done: {zip_path}")
    log(f"  zip size {os.path.getsize(zip_path) / 1e6:.1f} MB, {len(exported)} source files")
    log(f"  exe SHA-256 {sha256_of(exe)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
