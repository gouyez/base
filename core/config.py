from pathlib import Path
import sys, os

APP_VERSION = "3.6.1"

# resource helper similar to earlier single-file
def is_frozen():
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")

def resource_path(relative: str) -> Path:
    if is_frozen():
        base = Path(sys._MEIPASS)
        return base / relative
    return Path(relative).absolute()

# App dirs
LOCAL_APP_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "GmailHybrid"
MASTER_CHROME_DIR = LOCAL_APP_ROOT / "chrome_master"
CHROMES_DIR = LOCAL_APP_ROOT / "chromes"
PROFILES_DIR = LOCAL_APP_ROOT / "profiles"
TOKENS_DIR = Path("emails")

def ensure_master_extracted(log_fn=print):
    src = resource_path("chrome_master")
    if not src.exists():
        alt = Path(__file__).parent.parent / "chrome_master"
        if alt.exists():
            src = alt
        else:
            log_fn("[MASTER] chrome_master not found.")
            return False
    try:
        MASTER_CHROME_DIR.mkdir(parents=True, exist_ok=True)
        # if you want to actually copy master, keep previous logic here
        return True
    except Exception as e:
        log_fn(f"[MASTER] extract failed: {e}")
        return False

def ensure_tokens_dir():
    TOKENS_DIR.mkdir(exist_ok=True)
