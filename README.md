# Kisan Sahayak

A phone agent for farmers. Callers reach it through an Exotel Voicebot websocket. It answers in Kannada, Hindi, or English, reads mandi prices from the RaithaLine board, and stores a short summary of each call in Postgres against the caller's number.

## Run it

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Put the Sarvam, Gemini, Exotel, and Postgres values in `.env`. `DATABASE_URL` is the caller-memory database. On Railway, add a Postgres plugin and use the `DATABASE_URL` it injects.

```bash
python -m unittest tests.test_pipeline
python -m kisan.exotel_voice
```

The server listens on `127.0.0.1:8788`. For a phone call, expose that port and set the Exotel Voicebot URL to `https://<public-host>/exotel/voicebot`.

Mandi prices come from the RaithaLine dashboard at `RAITHA_API_URL`. Caller memory (place, crop, and earlier call summaries) comes from Postgres at `DATABASE_URL`.
