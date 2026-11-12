"""The core modules (hon, realtime, model, appliance) have no Home Assistant imports and are tested alone:
without Home Assistant installed, the package is registered by hand so its __init__ is not executed."""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

try:
    import homeassistant  # noqa: F401
except ImportError:
    for name, path in (("custom_components", ROOT / "custom_components"),
                       ("custom_components.hon_hub", ROOT / "custom_components" / "hon_hub")):
        module = types.ModuleType(name)
        module.__path__ = [str(path)]
        sys.modules[name] = module
