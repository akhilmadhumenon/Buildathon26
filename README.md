# Kisan Sahayak

A phone agent for farmers. Callers reach it through an Exotel Voicebot websocket. It answers in Kannada, Hindi, or English, reads mandi prices from the RaithaLine board, and stores a short summary against the caller's number.

## Run it

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Put the Sarvam, Gemini, and Exotel values in `.env`.

```bash
python -m unittest tests.test_pipeline
python -m kisan.exotel_voice
```

The server listens on `127.0.0.1:8788`. For a phone call, expose that port and set the Exotel Voicebot URL to `https://<public-host>/exotel/voicebot`.

Caller profiles and call summaries stay in `data/kisan.db`. Prices come from `RAITHA_API_URL`.
