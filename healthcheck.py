"""Container health probe: no credentials, ports or Discord requests."""
import json
import os
from pathlib import Path
import time


def healthy(path: Path, now: float | None = None) -> bool:
    try:
        data=json.loads(path.read_text())
        age=(time.time() if now is None else now)-data['timestamp']
        return data['ready'] is True and 0<=age<120
    except (OSError,ValueError,KeyError,TypeError):
        return False


if __name__=='__main__':
    raise SystemExit(0 if healthy(Path(os.getenv('HEALTH_FILE','/tmp/uwa-bot-health.json'))) else 1)
