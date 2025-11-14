"""
plugins package loader — loads built-in plugin modules in this package.
"""

import pkgutil
import importlib
import inspect
from pathlib import Path
from .base import Plugin

def discover_plugins(log=print):
    plugins = []
    pkg_dir = Path(__file__).parent

    # import all modules in this package (except base)
    for _, modname, ispkg in pkgutil.iter_modules([str(pkg_dir)]):
        if ispkg or modname == "base":
            continue
        try:
            mod = importlib.import_module(f"plugins.{modname}")
            for _, obj in inspect.getmembers(mod, inspect.isclass):
                if issubclass(obj, Plugin) and obj is not Plugin:
                    plugins.append(obj())
        except Exception as e:
            log(f"[PLUGIN][WARN] load {modname}: {e}")

    # ✅ sort by .order attribute (default = 0)
    plugins.sort(key=lambda p: getattr(p, "order", 0))

    return plugins
