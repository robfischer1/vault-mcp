"""Filesystem watcher for incremental index invalidation.

Uses watchdog to monitor the vault for .md file changes. On each event,
invalidates only the affected file's entries in the index rather than
triggering a full rebuild.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

if TYPE_CHECKING:
    from .index import VaultIndex

log = logging.getLogger(__name__)


class _VaultEventHandler(FileSystemEventHandler):
    """Routes .md file events to index invalidation."""

    def __init__(self, index: VaultIndex) -> None:
        super().__init__()
        self._index = index
        self._lock = threading.Lock()

    def _handle(self, event: FileSystemEvent) -> None:
        src = event.src_path if isinstance(event.src_path, str) else event.src_path.decode("utf-8", errors="replace")
        if not src.endswith('.md'):
            return
        path = Path(src)
        with self._lock:
            self._index.invalidate_file(path)
            log.debug("invalidated: %s", path)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._handle(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._handle(event)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._handle(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._handle(event)
            if hasattr(event, 'dest_path'):
                dest_path = event.dest_path
                dest_str = dest_path if isinstance(dest_path, str) else dest_path.decode("utf-8", errors="replace")
                if not dest_str.endswith('.md'):
                    return
                dest = Path(dest_str)
                with self._lock:
                    self._index.invalidate_file(dest)
                    log.debug("invalidated (move dest): %s", dest)


def start_watcher(index: VaultIndex) -> Any:
    """Start a background filesystem watcher on the vault directory.

    Returns the Observer instance (call .stop() to shut down).
    """
    handler = _VaultEventHandler(index)
    observer = Observer()
    observer.schedule(handler, str(index.vault), recursive=True)
    observer.daemon = True
    observer.start()
    log.info("watcher started on %s", index.vault)
    return observer
