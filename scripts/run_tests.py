"""Run every test on a private hidden Windows desktop.

Windows created by tests running on the hidden desktop cannot appear on the owner's
interactive screen, cannot take keyboard focus, and cannot flash, regardless of what
the code under test does.

Standard library and ctypes only.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import msvcrt
import os
import subprocess
import sys
import tempfile
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# Win32 Constants
GENERIC_ALL = 0x10000000
STARTF_USESTDHANDLES = 0x00000100
CREATE_NO_WINDOW = 0x08000000
CREATE_SUSPENDED = 0x00000004
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObjectExtendedLimitInformation = 9
JobObjectBasicProcessIdList = 3
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102
SYNCHRONIZE = 0x00100000

# Win32 Structures
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
        ("lpReserved2", ctypes.c_void_p),
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


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", wt.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wt.LARGE_INTEGER),
        ("LimitFlags", wt.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wt.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wt.DWORD),
        ("SchedulingClass", wt.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryLimit", ctypes.c_size_t),
        ("PeakJobMemoryLimit", ctypes.c_size_t),
    ]


WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

# Function Prototypes
user32.CreateDesktopW.restype = wt.HANDLE
user32.CreateDesktopW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.c_void_p]
user32.OpenDesktopW.restype = wt.HANDLE
user32.OpenDesktopW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.BOOL, wt.DWORD]
user32.CloseDesktop.restype = wt.BOOL
user32.CloseDesktop.argtypes = [wt.HANDLE]

user32.GetClipboardSequenceNumber.restype = wt.DWORD
user32.GetClipboardSequenceNumber.argtypes = []
user32.GetClipboardOwner.restype = wt.HWND
user32.GetClipboardOwner.argtypes = []

user32.EnumWindows.restype = wt.BOOL
user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.EnumDesktopWindows.restype = wt.BOOL
user32.EnumDesktopWindows.argtypes = [wt.HANDLE, WNDENUMPROC, wt.LPARAM]

user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.IsWindowVisible.restype = wt.BOOL
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowRect.restype = wt.BOOL
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetSystemMetrics.argtypes = [ctypes.c_int]

kernel32.CreateJobObjectW.restype = wt.HANDLE
kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
kernel32.SetInformationJobObject.restype = wt.BOOL
kernel32.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
kernel32.QueryInformationJobObject.restype = wt.BOOL
kernel32.QueryInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]
kernel32.AssignProcessToJobObject.restype = wt.BOOL
kernel32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]

kernel32.CreateProcessW.restype = wt.BOOL
kernel32.CreateProcessW.argtypes = [
    wt.LPCWSTR,
    wt.LPWSTR,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wt.BOOL,
    wt.DWORD,
    ctypes.c_void_p,
    wt.LPCWSTR,
    ctypes.POINTER(STARTUPINFOW),
    ctypes.POINTER(PROCESS_INFORMATION),
]
kernel32.ResumeThread.restype = wt.DWORD
kernel32.ResumeThread.argtypes = [wt.HANDLE]
kernel32.WaitForSingleObject.restype = wt.DWORD
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.GetExitCodeProcess.restype = wt.BOOL
kernel32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
kernel32.TerminateProcess.restype = wt.BOOL
kernel32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [wt.HANDLE]

# By default the suite runs with the Python running this script; pass --python (repeatable) for others.
DEFAULT_PYTHONS = [sys.executable]

# Track last run state (accessible for test verification)
last_created_desktop_name: str | None = None
last_owner_seen: set[tuple[int, str]] = set()
last_hidden_seen: set[int] = set()
last_workers: list[Worker] = []


def get_clipboard_sequence_number() -> int:
    """Return current sequence number for the clipboard."""
    return int(user32.GetClipboardSequenceNumber())


def get_clipboard_owner() -> int:
    """Return HWND of the current clipboard owner window."""
    return int(user32.GetClipboardOwner())


def get_window_pid(hwnd: int) -> int:
    """Return PID of the process owning the given window handle."""
    if not hwnd:
        return 0
    owner = wt.DWORD()
    user32.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(owner))
    return int(owner.value)


def get_job_process_ids(hJob: wt.HANDLE | None) -> set[int]:
    """Return all process IDs currently assigned to the Job Object."""
    if not hJob:
        return set()

    class JOBOBJECT_BASIC_PROCESS_ID_LIST(ctypes.Structure):
        _fields_ = [
            ("NumberOfAssignedProcesses", wt.DWORD),
            ("NumberOfProcessIdsInList", wt.DWORD),
            ("ProcessIdList", ctypes.c_size_t * 1024),
        ]

    info = JOBOBJECT_BASIC_PROCESS_ID_LIST()
    ok = kernel32.QueryInformationJobObject(
        hJob,
        JobObjectBasicProcessIdList,
        ctypes.byref(info),
        ctypes.sizeof(info),
        None,
    )
    if not ok:
        return set()
    count = min(int(info.NumberOfProcessIdsInList), 1024)
    return {int(info.ProcessIdList[i]) for i in range(count)}


def create_hidden_desktop(desk_name: str) -> wt.HANDLE | None:
    """Create a private hidden Windows desktop."""
    global last_created_desktop_name
    last_created_desktop_name = desk_name
    hdesk = user32.CreateDesktopW(desk_name, None, None, 0, GENERIC_ALL, None)
    if not hdesk:
        return None
    return hdesk


def close_hidden_desktop(hdesk: wt.HANDLE) -> bool:
    """Close the hidden desktop handle."""
    if not hdesk:
        return False
    return bool(user32.CloseDesktop(hdesk))


def create_job_object() -> wt.HANDLE | None:
    """Create a Windows Job Object configured to kill child processes when closed."""
    hJob = kernel32.CreateJobObjectW(None, None)
    if not hJob:
        return None
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    res = kernel32.SetInformationJobObject(
        hJob, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
    )
    if not res:
        kernel32.CloseHandle(hJob)
        return None
    return hJob


def get_window_title(hwnd: wt.HWND) -> str:
    """Safely get text/title of a window handle."""
    try:
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value
    except Exception:
        return ""


def windows_on_owner_desktop(pids: set[int]) -> list[tuple[int, str]]:
    """Enumerate visible on-screen windows belonging to target PIDs on the owner's desktop."""
    if not pids:
        return []

    screen = [user32.GetSystemMetrics(i) for i in (76, 77, 78, 79)]  # virtual screen x, y, w, h
    found: list[tuple[int, str]] = []

    @WNDENUMPROC
    def cb(hwnd, _):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in pids and user32.IsWindowVisible(hwnd):
            r = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            sx, sy, sw, sh = screen
            if r.right > sx and r.bottom > sy and r.left < sx + sw and r.top < sy + sh:
                title = get_window_title(hwnd)
                found.append((int(hwnd), title))
        return True

    # 1. Enumerate owner's interactive Default desktop if accessible
    h_default = user32.OpenDesktopW("Default", 0, False, GENERIC_ALL)
    if h_default:
        try:
            user32.EnumDesktopWindows(h_default, cb, 0)
        finally:
            user32.CloseDesktop(h_default)

    # 2. Also enumerate current thread desktop
    user32.EnumWindows(cb, 0)
    return found


def windows_on_hidden_desktop(hdesk: wt.HANDLE, pids: set[int]) -> list[int]:
    """Enumerate windows belonging to target PIDs created on the hidden desktop."""
    if not hdesk or not pids:
        return []

    found: list[int] = []

    @WNDENUMPROC
    def cb(hwnd, _):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in pids:
            found.append(int(hwnd))
        return True

    user32.EnumDesktopWindows(hdesk, cb, 0)
    return found


def label_for_python(py_path: str, index: int) -> str:
    lower = py_path.lower()
    if "miniconda" in lower:
        return "miniconda"
    if "pyenv" in lower:
        return "pyenv"
    base = os.path.basename(os.path.dirname(py_path))
    if base and base.lower() != "scripts":
        return base
    return f"py{index + 1}"


class Worker:
    def __init__(self, py_path: str, label: str, log_path: str, cmd: list[str]) -> None:
        self.py_path = py_path
        self.label = label
        self.log_path = log_path
        self.cmd = cmd
        self.hProcess: wt.HANDLE | None = None
        self.hThread: wt.HANDLE | None = None
        self.pid: int = 0
        self.log_file = None
        self.exit_code: int | None = None
        self.output: str = ""
        self.ran_line: str = ""
        self.status_line: str = ""
        self.elapsed: float = 0.0
        self.timed_out: bool = False
        self.owner_windows: set[tuple[int, str]] = set()
        self.hidden_windows: set[int] = set()


def start_worker(worker: Worker, desk_name: str, hJob: wt.HANDLE | None, repo_root: str) -> bool:
    """Launch a test process attached to the hidden desktop in suspended state, then assign to job."""
    worker.log_file = open(worker.log_path, "wb")
    log_handle = msvcrt.get_osfhandle(worker.log_file.fileno())
    os.set_handle_inheritable(log_handle, True)

    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(si)
    si.lpDesktop = f"WinSta0\\{desk_name}"
    si.dwFlags = STARTF_USESTDHANDLES
    si.hStdOutput = log_handle
    si.hStdError = log_handle

    pi = PROCESS_INFORMATION()
    cmdline = subprocess.list2cmdline(worker.cmd)

    flags = CREATE_NO_WINDOW | CREATE_SUSPENDED
    ok = kernel32.CreateProcessW(
        None,
        ctypes.create_unicode_buffer(cmdline),
        None,
        None,
        True,  # bInheritHandles
        flags,
        None,
        repo_root,
        ctypes.byref(si),
        ctypes.byref(pi),
    )
    if not ok:
        worker.log_file.close()
        worker.log_file = None
        return False

    worker.hProcess = pi.hProcess
    worker.hThread = pi.hThread
    worker.pid = int(pi.dwProcessId)

    if hJob:
        if not kernel32.AssignProcessToJobObject(hJob, pi.hProcess):
            err = ctypes.get_last_error()
            safe_print(
                f"Warning: AssignProcessToJobObject failed for PID {worker.pid}: error {err}",
                file=sys.stderr,
            )
            kernel32.TerminateProcess(pi.hProcess, 1)
            kernel32.CloseHandle(pi.hThread)
            kernel32.CloseHandle(pi.hProcess)
            worker.hProcess = None
            worker.hThread = None
            if worker.log_file:
                try:
                    worker.log_file.close()
                except Exception:
                    pass
                worker.log_file = None
            return False
    kernel32.ResumeThread(pi.hThread)

    return True


def finish_worker(worker: Worker, start_time: float) -> None:
    """Collect results and clean up open handles for a completed worker."""
    worker.elapsed = time.monotonic() - start_time
    if worker.log_file:
        try:
            worker.log_file.close()
        except Exception:
            pass
        worker.log_file = None

    if worker.hProcess:
        try:
            kernel32.CloseHandle(worker.hProcess)
        except Exception:
            pass
        worker.hProcess = None

    if worker.hThread:
        try:
            kernel32.CloseHandle(worker.hThread)
        except Exception:
            pass
        worker.hThread = None

    try:
        with open(worker.log_path, "r", encoding="utf-8", errors="replace") as f:
            worker.output = f.read()
    except Exception:
        worker.output = ""

    # Parse unittest summary lines
    for line in worker.output.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("Ran ") and "test" in trimmed:
            worker.ran_line = trimmed
        elif trimmed in ("OK", "FAILED") or trimmed.startswith(("OK (", "FAILED (")):
            worker.status_line = trimmed


def check_clipboard_change(
    hJob: wt.HANDLE | None,
    active_pids: set[int],
    last_seq: int,
    clipboard_violations: list[tuple[int, int]],
) -> int:
    """Check if clipboard changed. If changed by a test/job process, record violation."""
    seq = get_clipboard_sequence_number()
    if seq == last_seq:
        return last_seq

    owner_hwnd = get_clipboard_owner()
    owner_pid = get_window_pid(owner_hwnd)
    job_pids = get_job_process_ids(hJob) if hJob else set()
    all_target_pids = active_pids | job_pids

    if not owner_hwnd or owner_pid in all_target_pids:
        clipboard_violations.append((seq, owner_pid))
        safe_print(
            f"\nFAIL: A test changed the owner's clipboard (clipboard sequence number changed from {last_seq} to {seq}, owner PID: {owner_pid}).\n"
            f"Note: Team rule: fake clipboards only in tests.",
            file=sys.stderr,
        )
    else:
        safe_print(
            f"Note: the clipboard changed by another program (PID {owner_pid}), probably you; not a test."
        )

    return seq


def monitor_active_workers(
    active_workers: list[Worker],
    hdesk: wt.HANDLE,
    hJob: wt.HANDLE | None,
    timeout: float,
    start_time: float,
    owner_seen: set[tuple[int, str]],
    hidden_seen: set[int],
    clipboard_violations: list[tuple[int, int]],
    last_seq: int,
    poll_interval: float = 0.02,
) -> tuple[bool, int]:
    """
    Monitor active workers until all terminate or timeout.
    Returns (timed_out, last_clipboard_seq).
    """
    while active_workers:
        elapsed = time.monotonic() - start_time
        if elapsed > timeout:
            for w in active_workers:
                w.timed_out = True
                if w.hProcess:
                    try:
                        kernel32.TerminateProcess(w.hProcess, 1)
                    except Exception:
                        pass
                finish_worker(w, start_time)
            return True, last_seq

        # Collect all active PIDs including any grandchildren in the job
        active_pids = {w.pid for w in active_workers if w.pid}
        job_pids = get_job_process_ids(hJob) if hJob else set()
        all_pids = active_pids | job_pids

        # Check clipboard activity
        last_seq = check_clipboard_change(hJob, active_pids, last_seq, clipboard_violations)

        # Check for windows belonging to any process in the job
        for win in windows_on_owner_desktop(all_pids):
            owner_seen.add(win)

        for win in windows_on_hidden_desktop(hdesk, all_pids):
            hidden_seen.add(win)

        for w in active_workers:
            if not w.pid:
                continue
            for win in windows_on_owner_desktop({w.pid}):
                w.owner_windows.add(win)
            for win in windows_on_hidden_desktop(hdesk, {w.pid}):
                w.hidden_windows.add(win)

        # Check for process completion
        still_active: list[Worker] = []
        for w in active_workers:
            res = kernel32.WaitForSingleObject(w.hProcess, 0)
            if res == WAIT_OBJECT_0:
                code = wt.DWORD()
                kernel32.GetExitCodeProcess(w.hProcess, ctypes.byref(code))
                w.exit_code = int(code.value)
                finish_worker(w, start_time)
            else:
                still_active.append(w)

        active_workers = still_active
        time.sleep(poll_interval)

    return False, last_seq


def safe_print(text: str, file=None) -> None:
    """Print text safely, replacing unencodable characters if stdout encoding doesn't support them."""
    target = file if file is not None else sys.stdout
    try:
        target.write(text + "\n")
        target.flush()
    except UnicodeEncodeError:
        try:
            encoding = getattr(target, "encoding", "utf-8") or "utf-8"
            if hasattr(target, "buffer"):
                target.buffer.write(text.encode(encoding, errors="replace") + b"\n")
                target.buffer.flush()
            else:
                target.write(text.encode("ascii", errors="replace").decode("ascii") + "\n")
                target.flush()
        except Exception:
            pass


def default_test_tmp() -> str:
    """The folder the tests use as TEMP: VD_TEST_TMP if set, else venvdiscover-tests under the system temp folder."""
    return os.environ.get("VD_TEST_TMP") or os.path.join(tempfile.gettempdir(), "venvdiscover-tests")


def run_tests(
    pythons: list[str],
    test_args: list[str],
    workers_count: int,
    timeout: float,
    repo_root: str,
) -> int:
    """Run tests for all specified Pythons on a private hidden desktop."""
    global last_owner_seen, last_hidden_seen, last_workers
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # Ensure test temporary folder exists and is configured
    test_tmp = default_test_tmp()
    try:
        os.makedirs(test_tmp, exist_ok=True)
    except Exception:
        pass
    os.environ["TEMP"] = test_tmp
    os.environ["TMP"] = test_tmp

    # Ensure PYTHONPATH includes repo root and any test directories
    paths_to_add = [repo_root]
    for arg in test_args:
        if os.path.exists(arg):
            d = os.path.abspath(arg if os.path.isdir(arg) else os.path.dirname(arg))
            if d and d not in paths_to_add:
                paths_to_add.append(d)

    cur_pp = os.environ.get("PYTHONPATH", "")
    new_pp = ";".join(paths_to_add)
    if cur_pp:
        new_pp = f"{new_pp};{cur_pp}"
    os.environ["PYTHONPATH"] = new_pp

    # Create hidden desktop
    desk_name = f"vdtest-{os.getpid()}-{int(time.time() * 1000)}"
    hdesk = create_hidden_desktop(desk_name)
    if not hdesk:
        safe_print(f"ERROR: Failed to create hidden desktop: {ctypes.get_last_error()}", file=sys.stderr)
        return 1

    # Create Job Object
    hJob = create_job_object()

    # Track windows and clipboard
    owner_seen: set[tuple[int, str]] = set()
    hidden_seen: set[int] = set()
    clipboard_violations: list[tuple[int, int]] = []
    last_seq = get_clipboard_sequence_number()

    workers: list[Worker] = []
    temp_files: list[str] = []
    timed_out = False

    try:
        # Prepare workers
        for idx, py_path in enumerate(pythons):
            label = label_for_python(py_path, idx)
            with tempfile.NamedTemporaryFile(
                prefix=f"vdrun_{label}_", suffix=".log", dir=test_tmp, delete=False
            ) as f:
                log_path = f.name
            temp_files.append(log_path)

            if test_args:
                if test_args[0] == "discover":
                    rest = [a for a in test_args[1:] if a != "-v"]
                    cmd = [py_path, "-m", "unittest", "discover", "-v"] + rest
                elif len(test_args) == 1 and os.path.isdir(test_args[0]):
                    cmd = [py_path, "-m", "unittest", "discover", "-v", "-s", test_args[0]]
                elif len(test_args) == 1 and os.path.isfile(test_args[0]):
                    d = os.path.abspath(os.path.dirname(test_args[0]))
                    p = os.path.basename(test_args[0])
                    cmd = [py_path, "-m", "unittest", "discover", "-v", "-s", d, "-p", p]
                else:
                    rest = [a for a in test_args if a != "-v"]
                    cmd = [py_path, "-m", "unittest", "-v"] + rest
            else:
                cmd = [py_path, "-m", "unittest", "discover", "-v", "-s", "tests"]

            workers.append(Worker(py_path, label, log_path, cmd))

        overall_start = time.monotonic()

        if workers_count >= 2:
            # Concurrent execution
            active_batch: list[Worker] = []
            for w in workers:
                if not start_worker(w, desk_name, hJob, repo_root):
                    safe_print(f"ERROR: Failed to start worker for {w.label}", file=sys.stderr)
                    w.exit_code = 1
                    continue
                active_batch.append(w)

            timed_out, last_seq = monitor_active_workers(
                active_batch,
                hdesk,
                hJob,
                timeout,
                overall_start,
                owner_seen,
                hidden_seen,
                clipboard_violations,
                last_seq,
            )
        else:
            # Sequential execution
            for w in workers:
                start_time = time.monotonic()
                if not start_worker(w, desk_name, hJob, repo_root):
                    safe_print(f"ERROR: Failed to start worker for {w.label}", file=sys.stderr)
                    w.exit_code = 1
                    continue
                rem_timeout = max(0.1, timeout - (time.monotonic() - overall_start))
                timed_out, last_seq = monitor_active_workers(
                    [w],
                    hdesk,
                    hJob,
                    rem_timeout,
                    start_time,
                    owner_seen,
                    hidden_seen,
                    clipboard_violations,
                    last_seq,
                )
                if timed_out:
                    break

        # Final clipboard check
        all_worker_pids = {w.pid for w in workers if w.pid}
        last_seq = check_clipboard_change(hJob, all_worker_pids, last_seq, clipboard_violations)

    finally:
        # Ensure worker log files are closed
        for w in workers:
            if w.log_file:
                try:
                    w.log_file.close()
                except Exception:
                    pass
                w.log_file = None

        # Close Job and Desktop handles
        if hJob:
            try:
                kernel32.CloseHandle(hJob)
            except Exception:
                pass
        if hdesk:
            close_hidden_desktop(hdesk)

        # Cleanup temp log files
        for fpath in temp_files:
            try:
                if os.path.isfile(fpath):
                    os.unlink(fpath)
            except Exception:
                pass

    last_owner_seen = owner_seen
    last_hidden_seen = hidden_seen
    last_workers = workers

    # Print output per run
    for w in workers:
        safe_print("\n======================================================================")
        safe_print(f"[{w.label}] Output:")
        safe_print("----------------------------------------------------------------------")
        for line in w.output.splitlines():
            safe_print(f"[{w.label}] {line}")

    # Print summary
    safe_print("\n----------------------------------------------------------------------")
    safe_print("Summary:")
    all_passed = True
    for w in workers:
        if w.timed_out:
            all_passed = False
            last_lines = w.output.splitlines()[-20:] if w.output else []
            tail_txt = "\n".join(f"[{w.label}] {l}" for l in last_lines)
            safe_print(f"{w.label}: TIMED OUT after {timeout:.1f}s\nLast output:\n{tail_txt}")
        else:
            status_str = w.status_line or ("OK" if w.exit_code == 0 else "FAILED")
            ran_str = w.ran_line or f"Ran tests (exit {w.exit_code})"
            safe_print(f"{w.label}: {ran_str} | {status_str} | exit {w.exit_code} | {w.elapsed:.1f}s")
            safe_print(f"  windows of the test process on the OWNER's desktop: {len(w.owner_windows)}")
            safe_print(f"  windows of the test process on the hidden desktop: {len(w.hidden_windows)}")
            if w.exit_code != 0 or "FAILED" in status_str:
                all_passed = False

    # Guard checks
    safe_print(f"windows of the test process on the OWNER's desktop: {len(owner_seen)}")
    safe_print(f"windows of the test process on the hidden desktop: {len(hidden_seen)}")

    if owner_seen:
        all_passed = False
        titles = [t for _, t in owner_seen]
        safe_print(
            f"\nFAIL: {len(owner_seen)} window(s) appeared on the owner's desktop: {titles}",
            file=sys.stderr,
        )

    if clipboard_violations:
        all_passed = False

    return 0 if all_passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run test suite on a hidden Windows desktop to prevent window flashing."
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=3,
        help="Number of workers (default: 3). If >= 2, Pythons run concurrently.",
    )
    parser.add_argument(
        "--python",
        dest="pythons",
        action="append",
        help="Path to Python interpreter to run tests with (can be passed multiple times).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="Test run timeout in seconds (default: 600.0).",
    )
    parser.add_argument(
        "test_args",
        nargs="*",
        help="Optional test arguments or paths passed to unittest.",
    )

    args, unknown = parser.parse_known_args(argv)
    test_args = args.test_args + unknown

    # Repo root is the parent directory of scripts/
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)

    pythons = args.pythons or DEFAULT_PYTHONS

    # Validate Python interpreters
    for py in pythons:
        if not os.path.isfile(py):
            safe_print(f"ERROR: Python interpreter does not exist: {py}", file=sys.stderr)
            return 1

    return run_tests(
        pythons=pythons,
        test_args=test_args,
        workers_count=args.workers,
        timeout=args.timeout,
        repo_root=repo_root,
    )


if __name__ == "__main__":
    sys.exit(main())
