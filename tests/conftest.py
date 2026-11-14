import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Two placeholder clouds so routing logic can be exercised without real endpoints.
os.environ.setdefault("PROVIDERS", "verda,modal")
os.environ.setdefault("VERDA_BASE_URL", "http://verda.invalid/v1")
os.environ.setdefault("MODAL_BASE_URL", "http://modal.invalid/v1")
os.environ.setdefault("MODAL_STANDBY", "true")
os.environ.setdefault("VERDA_API_KEY", "test")
os.environ.setdefault("MODAL_API_KEY", "test")
