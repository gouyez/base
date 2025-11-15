# core/logger.py
import os
import threading
import time
from pathlib import Path
import getpass
import sys
import shutil

_lock = threading.RLock()

class FileLogger:
    def __init__(self, base_dir=None, max_lines=5000, prefix="log"):
        # base_dir: directory where logs/ will be created. If None, use script dir.
        if base_dir is None:
            base_dir = Path(__file__).resolve().parent.parent
        self.base_dir = Path(base_dir)
        self.logs_dir = self.base_dir / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.max_lines = max_lines
        self.prefix = prefix
        self._file = None
        self._lines = 0
        self._open_new_file()

    def _safe_username(self):
        try:
            name = getpass.getuser()
        except Exception:
            name = "unknown"
        return "".join(c for c in name if c.isalnum() or c in ("-", "_")).strip() or "user"

    def _timestamp(self):
        return time.strftime("%Y%m%d_%H%M%S")

    def _open_new_file(self):
        username = self._safe_username()
        ts = self._timestamp()
        fname = f"{self.prefix}_{username}_{ts}.txt"
        path = self.logs_dir / fname
        # ✅ Open in binary mode to avoid newline translation
        self._file_path = path
        self._file = open(path, "ab", buffering=0)
        self._lines = 0

    def _rotate(self):
        try:
            self._file.close()
        except Exception:
            pass
        ts = self._timestamp()
        rotated = self._file_path.with_name(self._file_path.stem + f"_rotated_{ts}.txt")
        try:
            self._file_path.replace(rotated)
        except Exception:
            try:
                shutil.copy2(self._file_path, rotated)
                self._file_path.unlink(missing_ok=True)
            except Exception:
                pass
        self._open_new_file()

    def write(self, line: str):
        """Write exactly one CRLF line (binary-safe, no extra blanks)."""
        # Always normalize to one CRLF
        clean_line = (line.rstrip("\r\n") + "\r\n").encode("utf-8", errors="replace")
        with _lock:
            try:
                self._file.write(clean_line)
                self._file.flush()
                try:
                    os.fsync(self._file.fileno())
                except Exception:
                    pass
                self._lines += 1
                if self._lines >= self.max_lines:
                    self._rotate()
            except Exception as e:
                try:
                    sys.stderr.buffer.write(f"Logger write failed: {e}\r\n".encode("utf-8"))
                except Exception:
                    pass


_singleton_logger = None

def get_file_logger(base_dir=None, max_lines=5000):
    global _singleton_logger
    if _singleton_logger is None:
        _singleton_logger = FileLogger(base_dir=base_dir, max_lines=max_lines)
    return _singleton_logger


def _summarize_for_gui(msg: str, max_len=300):
    s = msg.strip()
    return s[:max_len - 3] + "..." if len(s) > max_len else s


def get_logger(gui_callback=None, max_lines=5000, gui=True):
    """
    Returns a callable log_fn(msg: str) compatible with existing code.
    - Writes full timestamped lines to rotating files in ./logs/
    - Calls gui_callback(summary) if provided
    """
    file_logger = get_file_logger(max_lines=max_lines)

    def log_fn(msg):
        try:
            line = str(msg)
        except Exception:
            line = repr(msg)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        # Normalize line endings (prevent double newlines)
        line = line.rstrip("\r\n")
        full_line = f"{ts} {line}"
        try:
            file_logger.write(full_line)
        except Exception:
            pass
        try:
            print(full_line, flush=True)
        except Exception:
            pass
        if gui and gui_callback:
            try:
                gui_callback(_summarize_for_gui(line))
            except Exception:
                pass

    return log_fn
