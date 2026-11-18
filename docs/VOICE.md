# Voice answer contract

`POST /v1/voice/answer` is the only call the phone agent makes while a farmer is on the line. The desk builds the spoken cards in memory at startup, after Sync, and after a gate price is saved or cleared. The request does not fetch Agmarknet, Open-Meteo, or the Kisan Call Centre.

The phone agent speaks `say` exactly as it arrives. It does not translate it, round it, or fill in a number from anywhere else. If `ask` is set, `say` is that question, and the agent asks it. If `refused` is true, the agent speaks `say` and does not guess.

## Request

```json
{
  "text": "What is the tomato price in Kolar?",
  "phone": "9845011101",
  "language": "en",
  "crop": "tomato",
  "place": "Kolar",
  "sown_on": null
}
```

`text` is the transcript. `language` is `en`, `hi`, or `kn` (a tag such as `kn-IN` is accepted). `crop`, `place`, `phone`, and `sown_on` are optional. The crop has to be one of tomato, onion, potato, chilli, banana, ragi, maize, tur, paddy, groundnut. A crop or mandi outside that list is a refusal, not a guessed price.

## Response

Every response has these fields, in this order. Do not add fields, and do not rename them.

| Field | Meaning |
| --- | --- |
| `language` | `en`, `hi`, or `kn`. The language of `say`. |
| `intent` | `price`, `sell`, `harvest`, `rain`, `dose`, `clarify`, or `out_of_domain`. |
| `say` | The sentence to speak. Already in `language`. |
| `ask` | The same question as `say` when something is missing, otherwise `null`. |
| `refused` | `true` when this line will not answer. `say` is the refusal. |
| `facts` | Numbers the sentence was built from. Each item is `key`, `value`, `unit`, `as_of`. |
| `citation` | Where the numbers came from, or `null` on a refusal that has no source row. |

```json
{
  "language": "en",
  "intent": "price",
  "say": "RaithaLine: Tomato at Kolar APMC on 2026-11-16. Most sales were at Rs 1200/qtl, ranging from Rs 300 to Rs 2330 by grade. Advice: FPO pool to Binny Mill (FF&V) Bengaluru APMC. Transport Rs 140/qtl. Do not divert alone if a local agent advanced inputs. Source: Agmarknet.",
  "ask": null,
  "refused": false,
  "facts": [
    {"key": "modal", "value": 1200, "unit": "INR/qtl", "as_of": "2026-11-16"}
  ],
  "citation": "Agmarknet date-wise commodity report for Karnataka."
}
```

`facts[].unit` is `INR/qtl`, `tonnes`, `mm`, or `null`. Prices are rupees per quintal. Arrivals are metric tonnes.

## Phone agent

The Kisan Sahayak service sets `RAITHA_URL` to this desk. `kisan/raitha.py` posts the transcript and returns `say`. The fast reply and the price and harvest tools return that sentence unchanged. A model reply runs only when this call returns nothing. Do not put a model in front of `say`, and do not replace its numbers with the demo database.

A later change to this route has to keep these seven fields and this rule: the agent speaks `say` and does not invent a price, a date, or a dose.
