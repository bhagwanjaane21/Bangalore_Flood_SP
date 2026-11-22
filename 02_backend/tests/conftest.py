import sys
from pathlib import Path

# Allow `python -m pytest 02_backend/tests` from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
