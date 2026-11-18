# RaithaLine

A Karnataka feature-phone line for ten crops, split into two services in this repo. **RaithaLine** is the FPO desk. It holds the mandi prices and decides what may be said. **KisanCall** is the phone agent. It answers the farmer and reads those prices over HTTP. Each service has its own database. KisanCall does not open the desk file.

| | RaithaLine | KisanCall |
| --- | --- | --- |
| Role | FPO desk and price API | Phone agent |
| Branch | `main` | `thushara-test` |
| Railway | [raithaline-production.up.railway.app](https://raithaline-production.up.railway.app) | [kisancall-production.up.railway.app](https://kisancall-production.up.railway.app) |
| Process | `uvicorn app.main:app` | `python -m kisan.exotel_voice` |
| Health | `GET /v1/health` | `GET /health` |
| Store | `data/raitha.db` | `data/kisan.db` |

A push to `main` deploys the desk. A push to `thushara-test` deploys the phone agent.

## What a call does

The farmer calls the Exotel number. Exotel opens KisanCall at `GET /exotel/voicebot`, then the media websocket at `/exotel/media`. KisanCall hears the question and speaks one line in English, Hindi, or Kannada.

Prices come from the desk. On a cache miss KisanCall calls `GET /v1/health`, then `GET /v1/admin/board?crop=` for each loaded crop, and keeps that sheet for 10 minutes. Gemini speaks first when it answers. When it does not, KisanCall speaks the cached board price, including a nearby mandi when the caller asks about selling. Caller memory stays in `data/kisan.db`.

The desk also serves `POST /v1/voice/answer`. That response is the sentence to speak, with the numbers already filled in. The contract is [docs/VOICE.md](docs/VOICE.md). The running phone build reads the board instead of that route.

## Crops

Only these ten are on the line. A crop outside the list is a refusal, not a guessed price.

| Crop | Mandi |
| --- | --- |
| Tomato | Kolar |
| Onion | Hubballi |
| Potato | Hassan |
| Dry chilli | Hubballi |
| Banana | Bengaluru (Binny Mill) |
| Ragi | Mysuru |
| Maize | Davanagere |
| Tur | Kalaburagi |
| Paddy | Raichur |
| Groundnut | Chitradurga |

Tomato at Kolar is the only scored forecast, and it is 7 days. Nearby-mandi advice for tomato compares Kolar with Binny Mill. That trip is for the FPO to pool. A farmer who owes a local commission agent for inputs is not told to go alone.

Tomato and banana spoil quickly, so a soft price means leave the fruit on the vine, and a harvested load means sell. The other eight can go to a dry godown. Every price states the usual rate and, when the day has both, the grade range.

## RaithaLine

The desk is the screen in the FPO room and the API behind the call.

- The console at `GET /` shows the ten-crop board: usual price, grade range, arrivals, a nearby mandi, and whether the trip is worth it.
- Agmarknet series are stored in `data/raitha.db` and rebuilt into memory. The morning gate price (`POST /v1/admin/gate`) replaces a stale modal before the call batch. Arrivals and last year's week stay on the official series.
- Dose answers come only from a matching local Kisan Call Centre record. If nothing matches, the line refuses and names the Krishi Vigyan Kendra.
- Rain is the cached Open-Meteo reading. It is refreshed on a timer, not fetched during the call.
- A farmer is proposed for an outbound call only after opt-in, an open harvest window, and a person approving the row. Heavy rain and a load already in transit are held back.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 scripts/fetch_agmarknet.py   # already populated in data/raw if you are offline
python3 scripts/bootstrap.py
uvicorn app.main:app --port 8000
```

Open http://127.0.0.1:8000 for the FPO console. Health is `GET /v1/health`.

Routes the phone and the desk use:

| Route | Use |
| --- | --- |
| `GET /v1/admin/board?crop=` | Mandi card the phone reads |
| `POST /v1/voice/answer` | Spoken sentence, facts, and citation |
| `POST /v1/calls/inbound` | Start a call and return caller context |
| `POST /v1/calls/{session_id}/turn` | One turn: price, sowing, dose, in transit |
| `POST /v1/calls/{session_id}/complete` | Save the call and return the SMS text |
| `POST /v1/plans/weekly` | Market, weather, and climate claims for one sowing |
| `GET /v1/outbound/pending` | Approved calls the dialer has not finished |
| `POST /v1/admin/gate` | Live morning gate price |
| `POST /v1/admin/triggers/{id}/approve` | A person allows the outbound call |

The request and response shapes are in [docs/API.md](docs/API.md).

## KisanCall

The phone agent is on `thushara-test`, in the `kisan` package. Checkout that branch to run it. Caller profiles and call summaries stay in `data/kisan.db`. Set `RAITHA_API_URL` to the desk. An empty URL keeps the local demo prices.

```bash
git checkout thushara-test
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m kisan.exotel_voice
```

Locally the server binds `127.0.0.1:8788`. Point the Exotel Voicebot applet at `https://<public-host>/exotel/voicebot`. That returns the websocket `wss://<public-host>/exotel/media`. On Railway, `PORT` is set and the process listens on `0.0.0.0`.

`.env.example` on that branch names the keys. They are not committed.

| Variable | Use |
| --- | --- |
| `RAITHA_API_URL` | Desk origin. The phone reads `/v1/health` and `/v1/admin/board` from here |
| `KISAN_DB` | Caller database. Default `data/kisan.db` |
| `SARVAM_API_KEY` | Speech in and speech out |
| `GEMINI_API_KEY` | First attempt at the spoken reply |
| `EXOTEL_API_KEY`, `EXOTEL_API_TOKEN`, `EXOTEL_ACCOUNT_SID` | Exotel account |
| `EXOTEL_APP_ID`, `EXOTEL_EXOPHONE` | The voice applet and the number farmers call |

## What the line will not invent

A price, a date, or a dose has to come from the desk card, the Agmarknet series, the gate price, or a matching Kisan Call Centre row. If the crop is not on the line, or the source is missing, the reply says so.

## Docs

- [docs/PITCH.md](docs/PITCH.md) is the one-page account of the line.
- [docs/API.md](docs/API.md) is the desk contract.
- [docs/VOICE.md](docs/VOICE.md) is the seven-field spoken answer.
- [docs/architecture.md](docs/architecture.md) is how the desk chooses a price, a rain hold, and who may be called.
