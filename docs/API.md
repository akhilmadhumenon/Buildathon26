# RaithaLine voice contract

The voice agent speaks English, Hindi, or Kannada. This API owns the facts. Prices, dates, and advice in `facts` and in the SMS are filled from Agmarknet and the forecast. The voice model should read those strings and not introduce a number of its own.

Base URL in the demo: `http://127.0.0.1:8000`

## Crops

Only these ten ids are accepted. Anything else returns `unknown_crop`.

| id | English | Primary mandi |
| --- | --- | --- |
| tomato | Tomato | Kolar |
| onion | Onion | Hubballi |
| potato | Potato | Hassan |
| chilli | Dry chilli | Hubballi |
| banana | Banana | Bengaluru (Binny Mill) |
| ragi | Ragi | Mysuru |
| maize | Maize | Davanagere |
| tur | Tur | Kalaburagi |
| paddy | Paddy | Raichur |
| groundnut | Groundnut | Chitradurga |

Nearby-mandi advice exists for tomato only: Kolar versus the Bengaluru vegetable yard, at Rs 2 per quintal per kilometre over 70 km. That trip is for the FPO to pool and book. A farmer who owes a local commission agent for inputs is not told to divert alone.

A soft price is not one word. Tomato and banana: `delay_harvest` (leave the fruit on the vine for 3 to 4 days). Once that fruit is harvested, the advice becomes `sell_now`, because it lasts 3 to 5 days without cold storage. Ragi, tur, maize, dry chilli, groundnut, paddy, onion, and potato: `store` (dry godown).

Every price fact and every SMS states the modal and, when the day has both, the min–max grade spread. The voice agent reads `facts` and does not invent a third number.

Mandya ragi and Byadagi chilli did not have a long enough Agmarknet series to compare with last year, so those two use Mysuru and Hubballi.

## POST /v1/calls/inbound

```json
{ "phone": "9845011101", "language": "kn", "lat": null, "lon": null }
```

`language` may be `en`, `hi`, or `kn`. If omitted, the saved farmer language is used, otherwise Kannada.

Location: coordinates from the voice gateway when it has them, otherwise the saved district. If neither exists, `location.ask_village` is true and the agent should ask which village. This build does not look up a cell tower.

Response includes `session_id`, `active_sowing`, `last_summaries`, `open_trigger`, `facts`, and `ask`:

- `confirm_sowing` when this number already has a sowing
- `ask_crop_and_sow_date` when the number is known but no sowing is stored
- `pick_crop` when the number is new. `crops` lists the ten names

Seeded numbers:

- `9845011101` Kannada tomato grower in Kolar, with an earlier call
- `9845011102` Hindi onion grower in Hubballi, opted in
- `9845011103` Kannada onion grower in Hubballi, opted in, already in transit
- any other number is a new caller

## POST /v1/calls/{session_id}/turn

`intent` is one of:

- `confirm_sowing`
- `new_sowing` with `crop` and `sow_date` (`YYYY-MM-DD`), optional `quantity_qtl`, `district`, `village`
- `ask_price` optional `crop`
- `ready_to_send` optional `quantity_qtl` (marks the lot harvested and clears in-transit). For tomato or banana this returns a sell fact if the fruit is already picked.
- `in_transit` (suppresses proposed calls for this number) and `sale_complete` both end the harvest cycle. The response includes `climate_card`: how that district's 2040 heat shift moves this crop against drought-hardy ragi and tur, before the next sowing. The same text is in `facts` so it can be spoken.
- `opt_in` / `opt_out`
- `ask_dose` always refuses and names the Krishi Vigyan Kendra

A refusal looks like:

```json
{ "refused": true, "kind": "dose", "message": "...", "detail": "..." }
```

`kind` is `dose`, `out_of_domain`, `unknown_crop`, or `missing_series`.

A dose or fertiliser question is checked against the local Kisan Call Centre file first. If a Karnataka record for that crop shares the question's words, the response quotes that record and its id, and tells the farmer to confirm it with the Krishi Vigyan Kendra. If no record matches, the line refuses and does not invent a dose. `POST /v1/calls/{id}/turn` with `ask_dose` follows the same rule when `text` is set.

## POST /v1/calls/{session_id}/complete

```json
{ "transcript": "...", "summary": "optional", "language": "kn" }
```

Stores the call. Returns `sms.text` in the call language. The voice side sends the SMS.

## POST /v1/plans/weekly

Judge harness, with or without a phone.

```json
{
  "crop": "tomato",
  "sowing_date": "2026-08-19",
  "district": "Kolar",
  "soil_type": "red loam",
  "question": null
}
```

Returns `plan` and `claims`. Each claim has `agent` (`market`, `weather`, or `climate`), `claim`, and `citation`. The plan text is stitched from those claims. Soil is recorded as farmer-reported and is not checked against a soil grid.

If `question` asks for a dose, or is outside farming, the response is a refusal and there is no plan.

## GET /v1/outbound/pending

Calls a person has approved and the dialer has not finished. Proposed rows stay on the desk until approval.

## POST /v1/outbound/{id}/result

```json
{ "outcome": "farmer_yes", "note": "Can send 20 quintals tomorrow" }
```

Marks the row `called`.

## Desk

`GET /` is the FPO Aggregation Console. `POST /v1/admin/triggers/{id}/approve` is the human gate. `POST /v1/admin/triggers/{id}/suppress` holds a row back. Rebuild with `POST /v1/admin/triggers/rebuild`.

Agmarknet often lags 24 to 48 hours and can miss a weekend. Before the morning call batch, the FPO manager posts a live gate price:

```json
{ "crop": "tomato", "modal": 1600, "min_price": 400, "max_price": 2200 }
```

`POST /v1/admin/gate` stores it. Advice, facts, and SMS then use that modal and grade range instead of the lagged print. `DELETE /v1/admin/gate/{crop}` returns the crop to Agmarknet. Arrivals and the same-week comparison still come from the loaded series.
