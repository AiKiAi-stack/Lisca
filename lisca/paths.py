"""Keep writable data separate from the installed application package."""
import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
_source_root = PACKAGE_DIR.parent
_default_data = _source_root if (_source_root / "pyproject.toml").is_file() else Path.cwd()
DATA_DIR = Path(os.environ.get("LISCA_DATA_DIR", _default_data)).expanduser().resolve()
