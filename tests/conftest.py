import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Isolated data dir and the offline engine for every test run.
os.environ["OPENCHORUS_DATA"] = tempfile.mkdtemp(prefix="openchorus-test-")
os.environ.setdefault("OPENCHORUS_ENGINE", "demo")
# Never call a real Ollama from the test suite; planner tests inject a fake one.
os.environ["OPENCHORUS_PLANNER"] = "acestep"
