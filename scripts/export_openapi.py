"""Export the runtime transport contract without querying the database."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import app

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/export_openapi.py OUTPUT.json")
    Path(sys.argv[1]).write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")
