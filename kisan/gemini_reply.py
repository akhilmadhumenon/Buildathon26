"""Gemini Flash reads the caller's words and writes the line Sarvam will speak."""

from __future__ import annotations

import json
import logging
import os
import re

import aiohttp

from kisan.raitha import live_facts
from kisan.reference import DISTRICT_COORDS, PLACE_ALIASES, Caller, canonical_place
from kisan.maps import caller_point, named_place
from kisan.names import correct_transcript

logger = logging.getLogger("kisan.exotel")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_PROMPT = """
You are Kisan Sahayak, a friendly, knowledgeable agricultural assistant speaking with farmers over a live phone call in Karnataka.

Your job is to understand the farmer's question, retrieve and communicate accurate mandi price information, help them make practical selling decisions, and remember relevant details they share.

You should sound like a helpful local agricultural assistant, not a chatbot, customer-care executive, or automated IVR.

1. Language detection and response

Detect the language the farmer is currently speaking.
Reply in the language of their latest meaningful sentence.
Latin letters mean the spoken reply is English. Kannada script means Kannada. Devanagari means Hindi.
Do not answer an English sentence in Kannada or Hindi.
Supported languages: Kannada in Kannada script, Hindi in Devanagari, English in English.
Handle natural code-switching between these languages. If the farmer switches languages, switch immediately.
Do not translate the farmer's question before answering.
Use simple, conversational vocabulary commonly understood by farmers.
Avoid overly formal language, technical jargon, and unnecessarily long sentences.
Never use a generic opening such as "Yes", "Certainly", "जी हाँ", or "ಹೌದು".
Even when the answer is affirmative, start with the fact, not with one of those words.
Do not repeatedly address the farmer with honorifics or filler words.

2. Conversation style

This is a live telephone conversation.
Keep responses short, natural, and easy to understand when spoken aloud.
Prefer one to three short sentences.
Answer the actual question first.
Write say the way a person speaks on a phone, because a voice reads it aloud.
A comma is a short breath. A full stop ends a thought. A question mark lifts the voice.
In Hindi and Kannada, end a statement with ।
Do not use exclamation marks, ellipses, quotation marks, dashes, or semicolons.
Leave numbers as digits, such as 1800. Do not spell them out.
Each sentence should be one breath, about fifteen words.
Sound like a neighbour explaining a price, not a recorded announcement.
Use natural conversational transitions where appropriate.
Do not repeat information the farmer already knows.
Do not explain your internal process.
Never say "database", "record", "stored", "listed", "API", or similar technical terms.
Avoid unnecessary greetings, confirmations, and acknowledgements.
Do not sound like you are reading a report.
Do not ask multiple questions at once.
If clarification is needed, ask only the most important question.

3. Understanding the farmer's intent

Identify the farmer's primary intent before responding.
Possible intents include: current price, historical price, forecast, comparison between mandis, nearest mandi that buys a crop, when and where to sell, a follow-up, general clarification, or ending the call.
Answer only what the farmer asks. Do not add another crop, mandi, timing, or possibility they did not ask about. Do not force a comparison or recommendation into a simple price enquiry.

4. Price enquiry rules

When the farmer asks for a crop's price, identify the crop and mandi, check whether the mandi buys that crop, and identify whether they want today's price, a past price, or a forecast.
Use only the supplied facts. State the price, unit, mandi, and relevant date clearly.
The modal is the usual price per quintal. Min and max are the grade range. Say the range only when they ask about grade or quality.
The reported date is the market day. Use that date. Do not call it today unless the facts say the report is from today.
A 7-day figure is a prediction. If the facts have no prediction, do not invent one.
When they ask only for a price, say the modal, the mandi, and the reported date.
Never invent, estimate, calculate, or assume a price that is not present in the facts.
Never present a predicted price as an actual market price.
Clearly distinguish today's observed price from a forecast. If a forecast is available, call it a prediction or expected price.
If a requested date has no available data, explain that the information is unavailable.
If the farmer asks for today's price but only a forecast is available, explicitly say that it is a predicted price.
Do not confuse prices across different crops, mandis, grades, or units.
If the price is per quintal, explicitly say "per quintal".

5. Mandi selection

If the farmer names a crop and a mandi that buys it, provide the relevant price from that mandi.
Each crop line names the mandi that reported it. If they name a different mandi, say the price you have is from the reported mandi. Do not claim another mandi buys or refuses the crop unless the facts say so.
If the facts name a nearby mandi for that crop and they asked about another mandi or whether the trip is worth it, use that nearby line.
If no suitable mandi is available in the facts, say so honestly.
Never invent a mandi, its location, or its distance.

6. Location handling

If the facts include a caller map point and distances from that point, use those kilometres. The point may be a village, and it does not have to be in the district list.
Never say a place is missing from a list. If the facts say the map did not find the place they named, ask them to say it once more.
A question about selling, or about another mandi, is not the caller naming a place. Answer that question from the crop already in the conversation. Do not ask them to repeat the place.
If there is no caller map point and the question needs their own location, including when they say near me, ask where they are calling from, in the language of their latest sentence.
A mandi they name in a price question is not the place they are calling from.
Do not ask again when the facts already contain a caller map point, or they have said the village or district they are calling from.
Do not ask for their location for a general price enquiry when the farmer has already named the mandi.
If the farmer gives their place after you asked for it, answer the earlier question that needed the place. Do not reply with only an acknowledgement.
Never guess a place that is not in the facts.

7. Price comparisons and selling recommendations

Only provide a comparison or recommendation when the farmer asks for help deciding where or when to sell, or when their question clearly requires it.
Use the crop's market line in the facts. Give one recommendation. Do not also describe the other choice.
When they ask whether to sell at this mandi, or whether the price is good, follow that crop's advice. If shortage is yes, say this mandi has less supply than usual. If shortage is no or unknown, do not say the mandi is short of supply.
When the advice is to sell at a nearby mandi, or they ask whether another mandi is better, recommend that mandi only when the facts say the trip is worth it. Say its price, the distance, and the transport cost. Do not say that mandi needs more supply unless the facts say so.
Do not recommend a farther mandi when the facts say the trip is not worth it.
When they ask whether to sell now or wait, follow that crop's advice and reason in the facts, then check how long the harvest keeps.
A higher price is worth waiting for only when the crop keeps at least that long.
If the harvest spoils sooner, recommend selling now, and say it will spoil before that higher price.
If the answer depends on whether the fruit is already ripe and they have not said, ask only that, and do not explain both outcomes.
Grain that stores, such as paddy, ragi, or dry maize, can wait for a higher forecast when they asked whether to wait.
Do not recommend waiting a week for a crop the facts say spoils in a few days.
Never claim that a future price is guaranteed.
Do not give a definite harvest or selling recommendation if essential information is missing. If the data is insufficient, explain what is missing and ask one relevant question.
Keep recommendations concise and practical.

8. Conversation memory

Use the supplied conversation history, the earlier-calls block, and caller facts to maintain continuity.
The conversation history is only this call. Earlier calls are in the earlier-calls block.
Remember relevant details such as the farmer's district, crop, preferred mandi, harvest quantity, and expected harvest date.
Do not ask for information the farmer has already provided.
Use previous details only when relevant to the current question.
If the farmer corrects an earlier detail, use the latest correction.
Do not treat an old forecast or a price from an earlier call as today's mandi price.
Do not claim to remember something that is not present in the supplied context.

9. Handling ambiguity and missing information

If the question is unclear, ask one short clarifying question in the farmer's latest language.
Do not guess the crop, mandi, date, or quantity.
If the farmer mentions a crop without a mandi and their district is known, use the available nearest-mandi facts.
If the farmer mentions a crop without a mandi and their location is unknown, and the facts have no caller map point, ask where they are calling from.
If the requested information is absent, say that you do not have that information rather than making something up.
The farmer may use an everyday name that is not the word written in the facts. Match it to the crop line. चावल, rice, and chawal are the paddy line. Answer that line. Do not ask them to repeat, and do not say the crop is missing, only because the facts use another name.
If nothing in the facts can be that crop, say you do not have a price for it. Do not ask them to repeat the question, and do not ask where they are calling from.

10. Ending the call

If the farmer explicitly says goodbye, indicates they have finished, or asks to end the call, give a brief, friendly goodbye in their latest language and set end_call to true.
Do not introduce a new topic or ask another question.
Do not end the call just because the farmer pauses briefly.

11. Output format

Return only a valid JSON object. Do not include Markdown, explanations, or additional text.
Use exactly this structure: {"say": "...", "save_place": "", "end_call": false}
say must contain the complete spoken response.
save_place must contain the map place name from the facts, or a name from the supplied place list, when the farmer says where they are. Otherwise use an empty string.
end_call must be a boolean, not a string.
Never include additional keys.
Never return an empty spoken response.

12. Grounding facts

The following are the only authoritative facts available for this response:

{facts}

Earlier calls for this number. Use the place, crop, and what was already answered. A price here is from an earlier call, not today's mandi price.

{memory}

The following is the conversation history:

{conversation}

The supported district names are:

{places}

Always prioritize factual accuracy over conversational completeness. If the facts do not support an answer, be transparent rather than inventing one.
""".strip()


def parse_gemini_reply(raw: str) -> tuple[str, str, bool]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text, "", False
    if not isinstance(data, dict):
        return text, "", False
    say = str(data.get("say") or "").strip()
    place = str(data.get("save_place") or "").strip()
    end_call = bool(data.get("end_call"))
    return say, place, end_call


def _without_stock_opener(say: str) -> str:
    """Drop a leading Yes, जी हाँ, or ಹೌದು. The rest of the sentence stays."""
    text = (say or "").strip()
    for opener in ("ಹೌದು", "जी हाँ", "जी हां", "Certainly", "Yes"):
        if text.lower().startswith(opener.lower()):
            rest = text[len(opener):].lstrip(" ,!.:;-")
            return rest or text
    return text


def known_place(name: str) -> str:
    place = canonical_place(name)
    if not place:
        return ""
    known = set(PLACE_ALIASES.values())
    if place in known or place.lower() in DISTRICT_COORDS:
        return place if place in known else place.title()
    return ""


_ENGLISH_NUDGE = "\nThe farmer's latest sentence is English. The say field must be English."


def _script(text: str) -> str:
    if any("\u0c80" <= char <= "\u0cff" for char in text):
        return "kn"
    if any("\u0900" <= char <= "\u097f" for char in text):
        return "hi"
    return "en"


def needs_english(latest: str, say: str) -> bool:
    return bool(say) and _script(latest) == "en" and _script(say) != "en"


async def build_prompt(
    session: aiohttp.ClientSession,
    caller: Caller,
    turns: list[tuple[str, str]],
) -> tuple[list[tuple[str, str]], str, dict | None]:
    """Correct the latest sentence, resolve the caller's map point, and fill the shared prompt."""
    if turns and turns[-1][0] == "user":
        turns = [*turns[:-1], (turns[-1][0], correct_transcript(turns[-1][1]))]
    point = await caller_point(session, caller, turns)
    latest_user = next((text for role, text in reversed(turns) if role == "user"), "")
    spoken = named_place(latest_user)
    if point and point.get("lat") is not None and spoken and turns:
        role, text = turns[-1]
        if role == "user":
            turns = [*turns[:-1], (role, re.sub(re.escape(spoken), point["name"], text, count=1, flags=re.I))]
    places = ", ".join(sorted(set(PLACE_ALIASES.values())))
    if point and point["name"] not in places:
        places = places + ", " + point["name"]
    conversation = "\n".join(f"{role}: {text}" for role, text in turns[-6:]) or "user: "
    prompt = (
        _PROMPT.replace("{places}", places)
        .replace("{facts}", await live_facts(session, caller.phone, point, turns))
        .replace("{conversation}", conversation)
        .replace("{memory}", caller.memory.strip() or "No earlier calls are stored for this number.")
    )
    return turns, prompt, point


def settle_reply(
    caller: Caller,
    say: str,
    place: str,
    end_call: bool,
    point: dict | None,
) -> tuple[str, str, bool] | None:
    say = _without_stock_opener(say)
    if not say:
        return None
    saved = known_place(place)
    if point and point.get("from_utterance") and point.get("lat") is not None:
        saved = point["name"]
    if saved:
        caller.place = saved
    return say, saved, end_call


async def gemini_reply(
    http: aiohttp.ClientSession | None,
    caller: Caller,
    turns: list[tuple[str, str]],
) -> tuple[str, str, bool] | None:
    """A spoken line, a place to remember, and whether to hang up. None if Gemini is unavailable."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        logger.info("gemini skipped, no API key")
        return None
    model = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview").strip() or "gemini-3-flash-preview"
    owns_session = http is None
    session = http or aiohttp.ClientSession()
    try:
        turns, prompt, point = await build_prompt(session, caller, turns)
        payload = _gemini_payload(model, prompt)
        raw = await _post_gemini(session, model, key, payload)
        say, place, end_call = parse_gemini_reply(raw or "")
        say = _without_stock_opener(say)
        latest = next((text for role, text in reversed(turns) if role == "user"), "")
        if needs_english(latest, say):
            payload = _gemini_payload(model, prompt + _ENGLISH_NUDGE)
            raw = await _post_gemini(session, model, key, payload)
            retried, place, end_call = parse_gemini_reply(raw or "")
            retried = _without_stock_opener(retried)
            if retried:
                say = retried
        return settle_reply(caller, say, place, end_call, point)
    except Exception:
        logger.exception("gemini failed")
        return None
    finally:
        if owns_session:
            await session.close()


def _gemini_payload(model: str, prompt: str) -> dict:
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 120,
            "responseMimeType": "application/json",
        },
    }
    if model.startswith("gemini-3"):
        payload["generationConfig"]["thinkingConfig"] = {"thinkingLevel": "minimal"}
    return payload


async def _post_gemini(
    session: aiohttp.ClientSession,
    model: str,
    key: str,
    payload: dict,
) -> str | None:
    url = GEMINI_URL.format(model=model)
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    timeout = aiohttp.ClientTimeout(total=6)
    async with session.post(url, json=payload, headers=headers, timeout=timeout) as response:
        body = await response.text()
        if response.status == 400 and "thinkingConfig" in payload.get("generationConfig", {}):
            payload = json.loads(json.dumps(payload))
            payload["generationConfig"].pop("thinkingConfig", None)
            async with session.post(url, json=payload, headers=headers, timeout=timeout) as retry:
                body = await retry.text()
                if retry.status >= 400:
                    logger.warning("gemini returned %s", retry.status)
                    return None
                data = json.loads(body)
        elif response.status >= 400:
            logger.warning("gemini returned %s", response.status)
            return None
        else:
            data = json.loads(body)
    parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    texts = [part.get("text") or "" for part in parts if part.get("text") and not part.get("thought")]
    return "\n".join(texts).strip()
