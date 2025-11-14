from typing import Any
import builtins, sys, json

def safe_print(*args, **kwargs):
    try:
        print(*args, **kwargs)
    except Exception:
        try:
            sys.stdout.write(" ".join(str(a) for a in args) + "\n")
        except Exception:
            pass
