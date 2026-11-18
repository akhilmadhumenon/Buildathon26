"""Download Karnataka Agmarknet daily prices for the ten RaithaLine crops.

Source: api.agmarknet.gov.in date-wise commodity report (Agmarknet / data.gov.in).
One public captcha is solved per commodity-month. Raw JSON is kept so the
load step can be rerun without calling the API again. Months already on disk
are left as they are. The desk Sync button refreshes the open month.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agmarknet import fetch_pending


def main() -> None:
    def progress(event: dict) -> None:
        if event.get("crop"):
            print(
                f"{event['done'] + 1}/{event['total']} {event['crop']} {event['year']}-{event['month']:02d}",
                flush=True,
            )

    result = fetch_pending(refresh_open=False, progress=progress)
    print(
        f"fetch complete fetched={len(result['fetched'])} failed={len(result['failed'])}",
        flush=True,
    )
    for item in result["failed"]:
        print(f"  failed {item['crop']} {item['year']}-{item['month']:02d}: {item['detail']}", flush=True)


if __name__ == "__main__":
    main()
