"""Background scanner: discovers environments in three phases and streams events to a queue."""

from __future__ import annotations

import os
import queue
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from venvdiscover import packages, sizes
from venvdiscover.classify import probe, python_install
from venvdiscover.known import (
    known_env_paths,
    python_install_paths,
    registry_install_paths,
)
from venvdiscover.model import Env, Kind, longpath
from venvdiscover.places import Places
from venvdiscover.store import Settings
from venvdiscover.walker import default_scan_drives, local_drives, walk


@dataclass
class EnvFound:
    env: Env


@dataclass
class EnvUpdated:
    env: Env


@dataclass
class EnvGone:
    key: str


@dataclass
class Progress:
    text: str


@dataclass
class ScanDone:
    seconds: float
    total: int


@dataclass
class SizesDone:
    pass


class Scanner:
    def __init__(
        self,
        places: Places,
        settings: Settings,
        cached: list[Env],
        out: queue.Queue,
        *,
        environ: Mapping[str, str] | None = None,
        registry_paths: Callable[[], list[str]] = registry_install_paths,
        roots: list[str] | None = None,
        measure_sizes: bool = True,
    ) -> None:
        self.places = places
        self.settings = settings
        self.cached = list(cached)
        self.out = out
        self.environ = environ if environ is not None else os.environ
        self.registry_paths = registry_paths
        self.measure_sizes = measure_sizes

        if roots is not None:
            self.roots = list(roots)
        elif settings.drives is not None:
            self.roots = list(settings.drives)
        else:
            try:
                self.roots = default_scan_drives(local_drives())
            except Exception:
                self.roots = []

        self._cached_by_key = {e.key: e for e in self.cached}
        self._seen_keys: set[str] = set()
        self._lock = threading.Lock()
        self._progress_lock = threading.Lock()
        self._last_progress_time = 0.0
        self._stop_event = threading.Event()
        self._started = False
        self._threads_lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._sizes_queue: queue.Queue[Env | None] = queue.Queue()
        self._sizes_thread: threading.Thread | None = None
        self._coordinator_thread: threading.Thread | None = None
        self._start_time = 0.0

    def start(self) -> None:
        """Start the background discovery scanner."""
        with self._lock:
            if self._started:
                return
            self._started = True

        self._start_time = time.perf_counter()  # perf_counter: monotonic ticks only every ~15.6 ms on Windows

        t_sizes = threading.Thread(
            target=self._sizes_worker,
            name="ScannerSizesWorker",
            daemon=True,
        )
        self._sizes_thread = t_sizes
        with self._threads_lock:
            self._threads.append(t_sizes)
        t_sizes.start()

        t_coord = threading.Thread(
            target=self._run_scan,
            name="ScannerCoordinator",
            daemon=True,
        )
        self._coordinator_thread = t_coord
        with self._threads_lock:
            self._threads.append(t_coord)
        t_coord.start()

    def stop(self) -> None:
        """Signal all background scan threads to stop within 1 second."""
        self._stop_event.set()
        self._sizes_queue.put(None)

    def join(self, timeout: float | None = None) -> None:
        """Wait for all scan worker threads to finish."""
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            with self._threads_lock:
                alive = [t for t in self._threads if t.is_alive()]
            if not alive:
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
            for t in alive:
                if deadline is not None:
                    rem = max(0.0, deadline - time.monotonic())
                    t.join(min(rem, 0.05))
                else:
                    t.join(0.05)

    def _on_progress(self, root: str, visited: int, current: str) -> None:
        now = time.monotonic()
        with self._progress_lock:
            if now - self._last_progress_time >= 0.25:
                self._last_progress_time = now
                self.out.put(Progress(text=f"Scanning {root} \u2014 {visited:,} folders"))

    def _handle_env(self, env: Env) -> None:
        with self._lock:
            if env.key in self._seen_keys:
                return
            self._seen_keys.add(env.key)

        cached_env = self._cached_by_key.get(env.key)
        current_stamp = packages.package_stamp(env)

        if cached_env is not None and cached_env.stamp == current_stamp:
            env.packages = list(cached_env.packages)
            env.python_version = cached_env.python_version
            env.size = cached_env.size
            env.stamp = cached_env.stamp
            env.last_used = sizes.last_used(env)
        else:
            packages.fill(env)

        self.out.put(EnvFound(env=env))

        if self.measure_sizes and (env.size is None or cached_env is None or cached_env.stamp != current_stamp):
            self._sizes_queue.put(env)

    def _sizes_worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                env = self._sizes_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            if env is None:
                self._sizes_queue.task_done()
                break
            if self._stop_event.is_set():
                self._sizes_queue.task_done()
                break

            remaining = self._sizes_queue.unfinished_tasks
            now = time.monotonic()
            with self._progress_lock:
                if now - self._last_progress_time >= 0.25:
                    self._last_progress_time = now
                    self.out.put(Progress(text=f"Measuring sizes \u2014 {remaining} left"))

            try:
                if env.kind == Kind.CONDA_BASE:
                    sz = sizes.folder_size(env.path, should_stop=self._stop_event.is_set, skip_dirs=("envs",))
                else:
                    sz = sizes.folder_size(env.path, should_stop=self._stop_event.is_set)
                if not self._stop_event.is_set():
                    env.size = sz
                    self.out.put(EnvUpdated(env=env))
            except Exception:
                pass
            finally:
                self._sizes_queue.task_done()

    def _run_scan(self) -> None:
        # Phase 2: Known places
        try:
            for p in known_env_paths(self.places, self.environ, registry_paths=self.registry_paths()):
                if self._stop_event.is_set():
                    return
                try:
                    env = probe(p, self.places)
                    if env is not None:
                        self._handle_env(env)
                except OSError:
                    continue

            for p in python_install_paths(self.places, self.registry_paths()):
                if self._stop_event.is_set():
                    return
                try:
                    env = python_install(p)
                    if env is not None:
                        self._handle_env(env)
                except OSError:
                    continue
        except Exception:
            pass

        if self._stop_event.is_set():
            return

        # Phase 3: Walk roots in parallel (one thread per root)
        walk_threads: list[threading.Thread] = []
        unique_roots = list(dict.fromkeys(self.roots))

        def _walk_root(r: str) -> None:
            try:
                walk(
                    root=r,
                    places=self.places,
                    on_env=self._handle_env,
                    should_stop=self._stop_event.is_set,
                    skip_paths=[self.places.app_dir],
                    on_progress=lambda visited, current: self._on_progress(r, visited, current),
                )
            except Exception as exc:
                self.out.put(Progress(text=f"Couldn't scan {r}: {exc}"))

        for r in unique_roots:
            wt = threading.Thread(
                target=_walk_root,
                args=(r,),
                name=f"ScannerWalk-{r}",
                daemon=True,
            )
            walk_threads.append(wt)

        with self._threads_lock:
            self._threads.extend(walk_threads)

        for wt in walk_threads:
            wt.start()

        for wt in walk_threads:
            while wt.is_alive():
                wt.join(timeout=0.05)
                if self._stop_event.is_set():
                    return

        if self._stop_event.is_set():
            return

        # Discovery is complete: emit EnvGone for cached envs not seen whose folder is gone
        for cached_env in self.cached:
            if cached_env.key not in self._seen_keys and not os.path.isdir(longpath(cached_env.path)):
                self.out.put(EnvGone(key=cached_env.key))

        elapsed = time.perf_counter() - self._start_time
        total = len(self._seen_keys)
        self.out.put(ScanDone(seconds=elapsed, total=total))

        # Background sizes: wait for sizes queue to drain, then stop sizes worker
        if self.measure_sizes:
            while self._sizes_queue.unfinished_tasks > 0 and not self._stop_event.is_set():
                time.sleep(0.01)

        self._sizes_queue.put(None)
        if self._sizes_thread is not None:
            self._sizes_thread.join(timeout=1.0)

        if not self._stop_event.is_set():
            self.out.put(SizesDone())
