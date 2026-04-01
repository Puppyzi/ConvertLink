import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / "vendor"

if VENDOR.exists():
    sys.path.insert(0, str(VENDOR))

from converter_app.app import run


if __name__ == "__main__":
    run()
