"""Connections between envs and the system: environments.txt, Jupyter kernels, and .lnk shortcuts."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import struct

from venvdiscover.model import is_under, longpath, norm_key
from venvdiscover.places import Places

_LINK_CLSID = b"\x01\x14\x02\x00\x00\x00\x00\x00\xc0\x00\x00\x00\x00\x00\x00\x46"


def _read_env_lines(path: str) -> tuple[list[str], str] | None:
    """Read environments.txt preserving line endings (CRLF/LF) and exact byte encoding.

    Decodes strictly as UTF-8 first, falling back to mbcs. Returns (lines, encoding)
    or None if missing, unreadable or cannot be decoded.
    """
    lp = longpath(path)
    if not os.path.isfile(lp):
        return None
    try:
        with open(lp, "rb") as f:
            raw = f.read()
    except OSError:
        return None

    encoding: str | None = None
    text: str = ""
    for enc in ("utf-8", "mbcs"):
        try:
            text = raw.decode(enc)
            encoding = enc
            break
        except UnicodeDecodeError:
            continue

    if encoding is None:
        return None

    return text.splitlines(keepends=True), encoding


def remove_env_line(environments_txt: str, env_path: str) -> bool:
    """Remove a line matching env_path by norm_key from environments.txt atomically.

    Keeps other lines byte-for-byte and preserves their line endings. Returns True if a
    matching line was removed, False otherwise.
    """
    read_res = _read_env_lines(environments_txt)
    if read_res is None:
        return False
    lines, encoding = read_res

    target_key = norm_key(env_path)
    kept_lines: list[str] = []
    removed = False

    for line in lines:
        stripped = line.strip()
        if stripped and norm_key(stripped) == target_key:
            removed = True
        else:
            kept_lines.append(line)

    if not removed:
        return False

    lp = longpath(environments_txt)
    tmp_path = lp + f".tmp.{os.getpid()}"
    raw_out = "".join(kept_lines).encode(encoding)
    with open(tmp_path, "wb") as f:
        f.write(raw_out)

    os.replace(tmp_path, lp)
    return True


def add_env_line(environments_txt: str, env_path: str) -> None:
    """Append env_path to environments.txt if absent, creating the file if missing."""
    lp = longpath(environments_txt)
    target_key = norm_key(env_path)

    read_res = _read_env_lines(environments_txt)
    if read_res is not None:
        lines, encoding = read_res
        for line in lines:
            stripped = line.strip()
            if stripped and norm_key(stripped) == target_key:
                return  # already present
        has_crlf = any(l.endswith("\r\n") for l in lines)
        nl = "\r\n" if has_crlf else "\n"
        new_lines = list(lines)
        if new_lines and not (new_lines[-1].endswith("\n") or new_lines[-1].endswith("\r")):
            new_lines[-1] = new_lines[-1] + nl
        new_lines.append(env_path + nl)
        raw_out = "".join(new_lines).encode(encoding)
    else:
        parent_dir = os.path.dirname(lp)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        nl = "\n"
        encoding = "utf-8"
        raw_out = (env_path + nl).encode(encoding)

    tmp_path = lp + f".tmp.{os.getpid()}"
    with open(tmp_path, "wb") as f:
        f.write(raw_out)

    os.replace(tmp_path, lp)


def kernel_dirs(places: Places) -> list[str]:
    """Return standard Jupyter kernels directories."""
    return [
        os.path.join(places.appdata, "jupyter", "kernels"),
        os.path.join(places.programdata, "jupyter", "kernels"),
    ]


def shortcut_dirs(places: Places) -> list[str]:
    """Return Start Menu Programs directories for appdata and programdata."""
    return [
        os.path.join(places.appdata, "Microsoft", "Windows", "Start Menu", "Programs"),
        os.path.join(places.programdata, "Microsoft", "Windows", "Start Menu", "Programs"),
    ]


def find_kernels(env_path: str, dirs: list[str]) -> list[str]:
    """Find kernelspec folders whose kernel.json argv[0] is under env_path."""
    found: list[str] = []
    seen: set[str] = set()

    for d in dirs:
        lp = longpath(d)
        if not os.path.isdir(lp):
            continue
        try:
            with os.scandir(lp) as it:
                for entry in it:
                    if not entry.is_dir():
                        continue
                    kjson = os.path.join(entry.path, "kernel.json")
                    lp_kjson = longpath(kjson)
                    if not os.path.isfile(lp_kjson):
                        continue
                    try:
                        with open(lp_kjson, "r", encoding="utf-8", errors="replace") as f:
                            data = json.load(f)
                    except Exception:
                        continue  # broken kernel.json is ignored
                    argv = data.get("argv")
                    if isinstance(argv, list) and argv and isinstance(argv[0], str):
                        if is_under(argv[0], env_path):
                            kdir = os.path.abspath(entry.path)
                            key = norm_key(kdir)
                            if key not in seen:
                                seen.add(key)
                                found.append(kdir)
        except OSError:
            continue

    return found


@dataclass
class LnkInfo:
    target: str
    working_dir: str
    arguments: str


def read_lnk(path: str) -> LnkInfo | None:
    """Parse a Windows Shell Link (.lnk) file and return LnkInfo, or None if invalid."""
    try:
        lp = longpath(path)
        if not os.path.isfile(lp):
            return None
        with open(lp, "rb") as f:
            data = f.read()

        if len(data) < 76:
            return None

        header_size = struct.unpack_from("<I", data, 0)[0]
        if header_size != 0x4C:
            return None

        clsid = data[4:20]
        if clsid != _LINK_CLSID:
            return None

        link_flags = struct.unpack_from("<I", data, 0x14)[0]
        pos = 76

        # HasLinkTargetIDList (0x1)
        if link_flags & 0x01:
            if pos + 2 > len(data):
                return None
            id_list_size = struct.unpack_from("<H", data, pos)[0]
            pos += 2 + id_list_size
            if pos > len(data):
                return None

        target = ""
        # HasLinkInfo (0x2)
        if link_flags & 0x02:
            link_info_start = pos
            if link_info_start + 4 > len(data):
                return None
            link_info_size = struct.unpack_from("<I", data, link_info_start)[0]
            if link_info_size < 28 or link_info_start + link_info_size > len(data):
                return None
            header_size = struct.unpack_from("<I", data, link_info_start + 4)[0]
            local_base_path_offset = struct.unpack_from("<I", data, link_info_start + 16)[0]

            local_base_path = ""
            if header_size >= 36:
                unicode_offset = struct.unpack_from("<I", data, link_info_start + 28)[0]
                if unicode_offset != 0 and unicode_offset < link_info_size:
                    u_start = link_info_start + unicode_offset
                    u_end = u_start
                    while u_end + 1 < link_info_start + link_info_size:
                        if data[u_end : u_end + 2] == b"\x00\x00":
                            break
                        u_end += 2
                    try:
                        local_base_path = data[u_start:u_end].decode("utf-16le")
                    except Exception:
                        local_base_path = ""

            if not local_base_path and local_base_path_offset != 0 and local_base_path_offset < link_info_size:
                a_start = link_info_start + local_base_path_offset
                null_pos = data.find(b"\x00", a_start, link_info_start + link_info_size)
                a_end = null_pos if null_pos != -1 else link_info_start + link_info_size
                try:
                    local_base_path = data[a_start:a_end].decode("mbcs")
                except Exception:
                    local_base_path = data[a_start:a_end].decode("latin-1", errors="replace")

            target = local_base_path
            pos = link_info_start + link_info_size

        is_unicode = bool(link_flags & 0x80)

        def _read_string() -> str:
            nonlocal pos
            if pos + 2 > len(data):
                raise ValueError("truncated string length")
            char_count = struct.unpack_from("<H", data, pos)[0]
            pos += 2
            if is_unicode:
                byte_len = char_count * 2
                if pos + byte_len > len(data):
                    raise ValueError("truncated utf-16 string")
                s = data[pos : pos + byte_len].decode("utf-16le", errors="replace")
                pos += byte_len
            else:
                byte_len = char_count
                if pos + byte_len > len(data):
                    raise ValueError("truncated ansi string")
                try:
                    s = data[pos : pos + byte_len].decode("mbcs")
                except Exception:
                    s = data[pos : pos + byte_len].decode("latin-1", errors="replace")
                pos += byte_len
            return s

        # StringData entries in order:
        # NAME (0x4), RELATIVE_PATH (0x8), WORKING_DIR (0x10), ARGUMENTS (0x20), ICON_LOCATION (0x40)
        relative_path = ""
        working_dir = ""
        arguments = ""

        if link_flags & 0x04:
            _read_string()
        if link_flags & 0x08:
            relative_path = _read_string()
        if link_flags & 0x10:
            working_dir = _read_string()
        if link_flags & 0x20:
            arguments = _read_string()
        if link_flags & 0x40:
            _read_string()

        if not target and relative_path:
            target = relative_path

        return LnkInfo(target=target, working_dir=working_dir, arguments=arguments)
    except Exception:
        return None


def _mentions_path(field: str, env_path: str) -> bool:
    """Return True if field mentions env_path followed by '\\', '\"', a space, or end of string."""
    if not field:
        return False
    keys = [norm_key(env_path), os.path.normcase(env_path)]
    variants = [field, os.path.expandvars(field)]
    for v in variants:
        norm_v = os.path.normcase(v)
        for key in keys:
            pattern = re.escape(key) + r'(?:[\\/"\s]|$)'
            if re.search(pattern, norm_v):
                return True
    return False


def find_shortcuts(env_path: str, dirs: list[str]) -> list[str]:
    """Find .lnk files recursively whose target, working_dir or arguments mention env_path."""
    found: list[str] = []
    seen: set[str] = set()

    for d in dirs:
        lp = longpath(d)
        if not os.path.isdir(lp):
            continue
        for root, _, files in os.walk(lp):
            for file in files:
                if file.lower().endswith(".lnk"):
                    full_path = os.path.join(root, file)
                    lnk_info = read_lnk(full_path)
                    if lnk_info is None:
                        continue
                    if (
                        _mentions_path(lnk_info.target, env_path)
                        or _mentions_path(lnk_info.working_dir, env_path)
                        or _mentions_path(lnk_info.arguments, env_path)
                    ):
                        clean_path = os.path.abspath(full_path)
                        key = norm_key(clean_path)
                        if key not in seen:
                            seen.add(key)
                            found.append(clean_path)

    return found
