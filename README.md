# Kisan Sahayak

A phone agent for farmers. It answers in Kannada, Hindi, or English, reads mandi prices and harvest timing from the local database, and stores a summary against the caller's number. The next call from that number is answered with those earlier notes.

## What happens on a call

1. The caller dials your Indian number. LiveKit sends the call to this agent, including the caller's number (`sip.phoneNumber`).
2. The agent loads that number's profile and earlier summaries, then greets. If there was a previous call, the greeting brings it up.
3. A price question calls `get_mandi_price`. A harvest question calls `get_harvest_advice` after the agent has the district, crop, and sowing date.
4. When the call ends, the questions and answers are appended to the `calls` table for that number.
5. The next call from the same number sees that row. If the question is similar, the agent says what it told them last time, then looks up a fresh answer.

The browser page uses the same path. The number you type is stored on the session the same way a phone call's caller ID is.

## Run it

Python 3.10 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Put your LiveKit Cloud URL, API key, and secret in `.env`, plus a Sarvam API key from [sarvam.ai](https://www.sarvam.ai). New Sarvam accounts include a small speech credit.

```bash
python -m unittest tests.test_pipeline
python -m kisan.agent dev
```

In a second terminal:

```bash
source .venv/bin/activate
python -m kisan.web
```

Open http://127.0.0.1:8787, enter your mobile number, ask "what is the tomato price in Kolar", hang up, and call again with the same number.

You can also talk from this machine without a browser. Set `DEMO_CALLER_PHONE` in `.env` and run `python -m kisan.agent console`. Use the same value on the second run to hear the earlier call come back.

## Indian number

This process does not buy the number. Indian voice numbers need a business registered in India.

1. Create a LiveKit Cloud project and copy the SIP URI from the Telephony page. Use the Mumbai region if it is offered.
2. Rent a number from Plivo (about ₹200 a month, plus ₹0.38 a minute for each inbound minute) or start Exotel's 7-day trial, which includes one trial number and ₹500 of call credit. Plivo also gives $10 of signup credit, but the India number still needs the Certificate of Incorporation and GST certificate.
3. Point that provider's SIP trunk at the LiveKit SIP URI.
4. Create an inbound trunk and a dispatch rule in LiveKit. The templates are [sip/inbound-trunk.example.json](sip/inbound-trunk.example.json) and [sip/dispatch-rule.example.json](sip/dispatch-rule.example.json). Put the real number in the trunk file. After the trunk exists, put its trunk id into the dispatch rule. The rule's agent name must stay `kisan-agent`, matching `AGENT_NAME`.
5. Keep `python -m kisan.agent dev` running, or deploy the same command as a LiveKit agent. Call the number from a mobile. The agent reads your caller ID, answers, and writes the summary when you hang up. Call again from the same mobile.

LiveKit's command-line tool can create the trunk and the rule:

```bash
lk sip inbound create sip/inbound-trunk.example.json
lk sip dispatch create sip/dispatch-rule.example.json
```

## Database

SQLite file `data/kisan.db`. It is created on the first call.

Demo prices, newest day 17 Nov 2026: tomato at Kolar 1800 rupees a quintal and falling, ragi at Mandya 3200 and flat, maize at Davangere 2140 and rising, paddy at Gangavathi 2320 and rising, onion at Hubballi 2450 and falling, chilli at Bengaluru 14200 and flat.

Harvest timing uses a fixed crop duration plus the next three days of rain from Open-Meteo for a few Karnataka districts. The price rows are the stand-in for the mandi prediction engine. Replace `mandi_prices` with that engine later; the agent already calls `get_mandi_price` instead of guessing.
