# Cases the line refuses

## Dose

Request question: What pesticide dose should I spray on tomato?

```json
{
  "refused": true,
  "kind": "dose",
  "message": "RaithaLine does not give pesticide or fertilizer doses. Contact the nearest Krishi Vigyan Kendra.",
  "detail": "What pesticide dose should I spray on tomato?"
}
```

## Outside farming

Request question: Who won the cricket world cup?

```json
{
  "refused": true,
  "kind": "out_of_domain",
  "message": "That is outside this line. It can talk about the mandi price, the rain, and the crop on this farm.",
  "detail": "Who won the cricket world cup?"
}
```

## Crop not on the line

Request question: Should I sell coffee?

```json
{
  "refused": true,
  "kind": "unknown_crop",
  "message": "That crop is not on this line. Choose one of: Tomato, Onion, Potato, Dry chilli, Banana, Ragi, Maize, Tur, Paddy, Groundnut.",
  "detail": "coffee"
}
```

## Missing series

Same weekly-plan function, with an empty price table.

```json
{
  "refused": true,
  "kind": "missing_series",
  "message": "There is no mandi series for that crop, so this line will not guess a price.",
  "detail": "tomato"
}
```

Krishi Vigyan Kendra is the place named when someone asks for a dose.
