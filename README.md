# RaithaLine

Backend for a Karnataka feature-phone line covering ten crops. The voice agent is a separate service. It calls this API for caller context, prices, the weekly plan, and the SMS text.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 scripts/fetch_agmarknet.py   # already populated in data/raw if you are offline
python3 scripts/bootstrap.py
uvicorn app.main:app --port 8000
```

Open http://127.0.0.1:8000 for the FPO Aggregation Console.

The partner contract is in [docs/API.md](docs/API.md). The one-page pitch is [docs/PITCH.md](docs/PITCH.md).
