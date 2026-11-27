"""Keep scoring tests off the default calls.jsonl log path."""

import os
from pathlib import Path

os.environ["TRACECHECK_LOG_FILE"] = str(Path(__file__).resolve().parent / ".calls.jsonl")
