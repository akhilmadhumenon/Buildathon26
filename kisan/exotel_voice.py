"""Exotel Voicebot websocket. Callers of 02241642353 reach this instead of a forwarded mobile."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from urllib.parse import urlencode

import aiohttp
from aiohttp import web
from dotenv import load_dotenv

from kisan.advice import (
    GOODBYE,
    facts_for_question,
    format_caller_context,
    harvest_answer,
    mandi_answer,
    related_calls,
    should_answer,
    spoken_mandi,
)
from kisan.names import correct_transcript
from kisan.audio import CHUNK_BYTES, PcmBuffer, audio_chunks, wav_to_pcm
from kisan.db import Database, normalize_phone, now_iso
from kisan.language import SPEECH_PACE, SPEECH_PITCH, detect_language, speech_segments, voice_for
from kisan.summary import render_summary

load_dotenv()

logger = logging.getLogger("kisan.exotel")

SAMPLE_RATE = 8000
SARVAM_STT_URL = "wss://api.sarvam.ai/speech-to-text-realtime/ws"
SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
SARVAM_TTS_WS = "wss://api.sarvam.ai/text-to-speech/ws?model=bulbul:v4-flash&send_completion_event=true"
SARVAM_CHAT_URL = "https://api.sarvam.ai/v2/chat/completions"

INSTRUCTIONS = """
You are Kisan Sahayak, a phone helper for farmers in Karnataka.
Answer in the caller's latest language, in the words that fit the question.
Never use lists, markdown, or symbols. In Hindi and Kannada, write in that script.
The caller may speak Kannada, Hindi, or English, and may switch during the call.
Answer in the language of the caller's latest sentence.
If a fact is included for this turn, say that fact and do not call a tool.
Otherwise use tools for prices and harvest advice. Do not invent a price.
Earlier calls are notes only. Mention one only when this question is about the same crop and the same place or harvest. If it is a different question, ignore the notes and do not say that you remember.
If a harvest question is missing the district, crop, or sowing date, ask for the missing piece kindly.
When the caller gives a district, crop, or sowing date, save it with save_farmer_profile.
Prices and crop durations are demo figures.

{context}
""".strip()

GREETING = {
    "en": "Namaste! How may I help you?",
    "hi": "नमस्ते! मैं आपकी क्या मदद कर सकती हूँ?",
    "kn": "ನಮಸ್ಕಾರ! ನಾನು ನಿಮಗೆ ಹೇಗೆ ಸಹಾಯ ಮಾಡಲಿ?",
}

RETRY = {
    "en": "Sorry, please say that once more.",
    "hi": "माफ़ कीजिए, एक बार फिर बोलिए।",
    "kn": "ಕ್ಷಮಿಸಿ, ಇನ್ನೊಮ್ಮೆ ಹೇಳಿ.",
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_mandi_price",
            "description": "Look up the latest mandi price and recent change for a crop.",
            "parameters": {
                "type": "object",
                "properties": {
                    "crop": {"type": "string"},
                    "mandi": {"type": "string", "description": "Mandi or district, such as Kolar or Mandya."},
                },
                "required": ["crop", "mandi"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_harvest_advice",
            "description": "Say whether a crop is ready to harvest from the sowing date, crop calendar, and rain.",
            "parameters": {
                "type": "object",
                "properties": {
                    "crop": {"type": "string"},
                    "district": {"type": "string"},
                    "sown_on": {"type": "string", "description": "Sowing date as YYYY-MM-DD."},
                },
                "required": ["crop", "district", "sown_on"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_farmer_profile",
            "description": "Store the farmer district, crop, or sowing date against this phone number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "district": {"type": "string"},
                    "crop": {"type": "string"},
                    "sown_on": {"type": "string"},
                    "name": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_previous_calls",
            "description": "Find earlier calls from this phone number that match the question.",
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
        },
    },
]


class ExotelCall:
    def __init__(self, socket: web.WebSocketResponse, db: Database) -> None:
        self.socket = socket
        self.db = db
        self.phone = ""
        self.call_sid = ""
        self._saved = False
        self._ending = False
        self.stream_sid = ""
        self.language = "en"
        self.started_at = now_iso()
        self.turns: list[tuple[str, str]] = []
        self._turn = 0
        self._ignore_until = 0.0
        self._held: tuple[str, str] | None = None
        self._last_heard = ""
        self._reply_task: asyncio.Task[None] | None = None
        self._partial_task: asyncio.Task[None] | None = None
        self._hold_task: asyncio.Task[None] | None = None
        self.stt: aiohttp.ClientWebSocketResponse | None = None
        self.speech: SpeechOut | None = None
        self.http: aiohttp.ClientSession | None = None
        self.cached: dict[str, bytes] = {}

    def _mark_played(self, pcm: bytes) -> None:
        now = time.perf_counter()
        self._ignore_until = max(self._ignore_until, now) + len(pcm) / float(SAMPLE_RATE * 2)

    def _hearing(self) -> bool:
        return time.perf_counter() >= self._ignore_until + 0.25

    async def send_audio(self, pcm: bytes, turn: int) -> None:
        for piece in audio_chunks(pcm):
            if turn != self._turn or self.socket.closed:
                return
            self._mark_played(piece)
            await self.socket.send_json(
                {
                    "event": "media",
                    "stream_sid": self.stream_sid,
                    "media": {"payload": base64.b64encode(piece).decode("ascii")},
                }
            )

    async def clear_playback(self) -> None:
        self._turn += 1
        if self.stream_sid and not self.socket.closed:
            await self.socket.send_json({"event": "clear", "stream_sid": self.stream_sid})

    async def speak(self, text: str) -> None:
        text = " ".join(text.split())
        if not text:
            return
        self.turns.append(("assistant", text))
        turn = self._turn
        pcm = await synthesize(text, self.language)
        pcm += b"\x00" * int(SAMPLE_RATE * 0.45) * 2
        if turn == self._turn:
            await self.send_audio(pcm, turn)

    async def play_cached(self, text: str, key: str) -> None:
        pcm = self.cached.get(key)
        self.turns.append(("assistant", text))
        if not pcm:
            await self.speak(text)
            return
        await self.send_audio(pcm, self._turn)

    async def greet(self) -> None:
        self.language = "en"
        if self.phone:
            self.db.save_farmer(self.phone)
        await self.play_cached(GREETING["en"], "greet-en")

    async def reply_to(self, text: str, language_hint: str | None) -> None:
        started = time.perf_counter()
        self.language = detect_language(text, language_hint, current=self.language)
        logger.info("language %s", self.language)
        self.turns.append(("user", text))
        try:
            answer, end_call = await answer_caller(self.http, self.db, self.phone, self.turns, self.language)
        except asyncio.TimeoutError:
            logger.warning("chat timed out for %s", self.phone)
            answer, end_call = RETRY.get(self.language, RETRY["en"]), False
        except Exception:
            logger.exception("reply failed")
            answer, end_call = RETRY.get(self.language, RETRY["en"]), False
        self.language = detect_language(answer, current=self.language)
        logger.info("answer ready in %.2fs: %s", time.perf_counter() - started, answer[:160])
        await self.speak(answer)
        if end_call or answer in GOODBYE.values():
            await self.end_call()

    def _cancel_task(self, task: asyncio.Task[None] | None) -> None:
        if task and not task.done():
            task.cancel()

    def _cancel_reply(self) -> None:
        self._cancel_task(self._reply_task)
        self._cancel_task(self._partial_task)
        self._cancel_task(self._hold_task)

    def _begin_reply(self, transcript: str, hint: str) -> None:
        transcript = correct_transcript(transcript)
        if _is_echo(transcript):
            logger.info("ignored echo %s", transcript[:80])
            return
        if not should_answer(transcript) or transcript == self._last_heard:
            if transcript != self._last_heard:
                logger.info("ignored %s", transcript[:80])
            return
        logger.info("heard %s", transcript[:120])
        self._last_heard = transcript
        replacing = self._reply_task is not None and not self._reply_task.done()
        self._cancel_task(self._reply_task)
        self._reply_task = asyncio.create_task(self._reply_after_clear(transcript, hint, replacing))

    async def _reply_after_clear(self, transcript: str, hint: str, replacing: bool) -> None:
        if replacing:
            await self.clear_playback()
        await self.reply_to(transcript, hint)

    def _hold(self, transcript: str, hint: str) -> None:
        transcript = correct_transcript(transcript)
        if _is_echo(transcript) or not should_answer(transcript):
            logger.info("ignored %s", transcript[:80])
            return
        logger.info("holding %s", transcript[:120])
        self._held = (transcript, hint)
        if self._hold_task is None or self._hold_task.done():
            self._hold_task = asyncio.create_task(self._release_held())

    async def _release_held(self) -> None:
        while not self._hearing() and not self._ending:
            await asyncio.sleep(0.05)
        held = self._held
        self._held = None
        if held and not self._ending:
            self._begin_reply(held[0], held[1])

    def _note_partial(self, transcript: str, hint: str) -> None:
        if _is_echo(transcript) or not should_answer(transcript):
            return
        self._cancel_task(self._partial_task)
        self._partial_task = asyncio.create_task(self._answer_partial(transcript, hint))

    async def _answer_partial(self, transcript: str, hint: str) -> None:
        await asyncio.sleep(0.35)
        if self._ending:
            return
        if not self._hearing():
            self._hold(transcript, hint)
            return
        self._begin_reply(transcript, hint)

    async def on_stt(self, event: dict) -> None:
        if self._ending:
            return
        kind = event.get("event")
        if kind == "error":
            logger.warning("stt error %s", event.get("message") or event)
            return
        if kind not in {"transcript.final", "transcript.partial", "vad.speech_start", "vad.speech_end"}:
            logger.info("stt %s", kind or str(event)[:160])
        if kind in {"transcript.final", "transcript.partial"}:
            transcript = str(event.get("text") or "").strip()
            hint = str(event.get("language") or "")
            if not transcript:
                return
            if kind == "transcript.partial":
                self._note_partial(transcript, hint)
                return
            self._cancel_task(self._partial_task)
            if not self._hearing():
                self._hold(transcript, hint)
                return
            self._begin_reply(transcript, hint)
            return
        if kind != "vad.speech_start" or not self._hearing():
            return
        self._cancel_task(self._reply_task)
        self._cancel_task(self._partial_task)
        await self.clear_playback()

    async def end_call(self) -> None:
        self._ending = True
        wait = max(0.0, self._ignore_until - time.perf_counter()) + 0.2
        await asyncio.sleep(wait)
        logger.info("ending call %s", self.phone or "unknown")
        await _hangup_exotel(self.call_sid)
        if not self.socket.closed:
            await self.socket.close()

    def save(self) -> None:
        if self._saved:
            return
        if not self.phone:
            logger.warning("call ended without a caller number; summary not stored")
            return
        summary, questions, answers = render_summary(self.turns)
        call_id = self.db.append_call(
            self.phone,
            summary=summary,
            questions=questions,
            answers=answers,
            language=self.language,
            started_at=self.started_at,
        )
        logger.info("saved exotel call %s for %s", call_id, self.phone)
        self._saved = True


async def _hangup_exotel(call_sid: str) -> None:
    """Ask Exotel to drop the line after goodbye, so the minute stops billing."""
    sid = os.environ.get("EXOTEL_ACCOUNT_SID", "")
    key = os.environ.get("EXOTEL_API_KEY", "")
    token = os.environ.get("EXOTEL_API_TOKEN", "")
    host = os.environ.get("EXOTEL_SUBDOMAIN", "api.exotel.com")
    if not (call_sid and sid and key and token):
        return
    url = f"https://{host}/v1/Accounts/{sid}/Calls/{call_sid}.json"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                data={"Status": "completed"},
                auth=aiohttp.BasicAuth(key, token),
                timeout=aiohttp.ClientTimeout(total=8),
            ) as response:
                if response.status >= 400:
                    logger.warning("exotel hangup returned %s", response.status)
    except Exception:
        logger.exception("exotel hangup failed")


def run_tool(db: Database, phone: str, name: str, arguments: dict) -> str:
    if name == "get_mandi_price":
        return mandi_answer(db, str(arguments.get("crop") or ""), str(arguments.get("mandi") or ""))
    if name == "get_harvest_advice":
        crop = str(arguments.get("crop") or "")
        district = str(arguments.get("district") or "")
        sown_on = str(arguments.get("sown_on") or "")
        if phone:
            db.save_farmer(phone, crop=crop or None, district=district or None, sown_on=sown_on or None)
        return harvest_answer(db, crop=crop, district=district, sown_on=sown_on)
    if name == "save_farmer_profile":
        record = db.save_farmer(
            phone,
            name=str(arguments.get("name") or "") or None,
            district=str(arguments.get("district") or "") or None,
            crop=str(arguments.get("crop") or "") or None,
            sown_on=str(arguments.get("sown_on") or "") or None,
        )
        return (
            f"Saved district {record.get('district') or 'unknown'}, "
            f"crop {record.get('crop') or 'unknown'}, sown on {record.get('sown_on') or 'unknown'}."
        )
    if name == "recall_previous_calls":
        return related_calls(db, phone, str(arguments.get("question") or ""))
    return "That tool is not available."


async def answer_caller(
    http: aiohttp.ClientSession | None,
    db: Database,
    phone: str,
    turns: list[tuple[str, str]],
    language: str = "en",
) -> tuple[str, bool]:
    question = turns[-1][1] if turns and turns[-1][0] == "user" else ""
    earlier = [text for role, text in turns[:-1] if role == "user"]
    heard = await gemini_reply(http, db, phone, turns)
    if heard:
        say, place, end_call = heard
        if phone and place:
            db.save_farmer(phone, district=place)
        return say, end_call
    quick = spoken_mandi(db, question, language, phone, earlier)
    if quick:
        return quick, quick in GOODBYE.values()
    facts = facts_for_question(db, question)
    system = INSTRUCTIONS.format(context=format_caller_context(db, phone))
    if facts:
        system += (
            "\n\nFact for this turn. Say it in the caller's language, in one short sentence. "
            f"Do not call a tool.\n{facts}"
        )
    messages: list[dict] = [{"role": "system", "content": system}]
    for role, text in turns:
        messages.append({"role": role, "content": text})
    for _ in range(2):
        message = await chat(http, messages, use_tools=not facts)
        tool_calls = message.get("tool_calls") or []
        content = str(message.get("content") or "").strip()
        if not tool_calls:
            text = content or "I could not look that up. Please say the crop and the mandi again."
            return text, text in GOODBYE.values()
        if facts:
            text = content or facts
            return text, text in GOODBYE.values()
        messages.append(message)
        for call in tool_calls:
            function = call.get("function") or {}
            name = function.get("name") or ""
            raw_arguments = function.get("arguments") or "{}"
            if isinstance(raw_arguments, str):
                try:
                    arguments = json.loads(raw_arguments)
                except json.JSONDecodeError:
                    arguments = {}
            else:
                arguments = raw_arguments
            result = run_tool(db, phone, name, arguments if isinstance(arguments, dict) else {})
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or name,
                    "content": result,
                }
            )
    return "Please say that once more.", False


async def chat(http: aiohttp.ClientSession | None, messages: list[dict], *, use_tools: bool) -> dict:
    payload: dict = {
        "model": os.environ.get("SARVAM_LLM_MODEL", "sarvam-105b"),
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 120,
    }
    if use_tools:
        payload["tools"] = TOOLS
        payload["tool_choice"] = "auto"
    owns_session = http is None
    session = http or aiohttp.ClientSession()
    try:
        async with session.post(
            SARVAM_CHAT_URL,
            json=payload,
            headers=_sarvam_headers(),
            timeout=aiohttp.ClientTimeout(total=2.5),
        ) as response:
            body = await response.text()
            if response.status >= 400:
                raise RuntimeError(f"Sarvam chat failed ({response.status}): {body[:300]}")
            data = json.loads(body)
    finally:
        if owns_session:
            await session.close()
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    return message


async def synthesize(text: str, language: str) -> bytes:
    segments = speech_segments(text, language)
    if not segments:
        return b""
    pause = b"\x00" * int(SAMPLE_RATE * 0.14) * 2
    pieces: list[bytes] = []
    async with aiohttp.ClientSession() as session:
        for index, segment in enumerate(segments):
            pcm = await _synthesize_segment(session, segment, language)
            if index:
                pieces.append(pause)
            pieces.append(pcm)
    return b"".join(pieces)


async def _synthesize_segment(session: aiohttp.ClientSession, text: str, language: str) -> bytes:
    code, speaker = voice_for(language)
    payload = {
        "text": text,
        "target_language_code": code,
        "speaker": speaker,
        "model": "bulbul:v4-flash",
        "pace": SPEECH_PACE,
        "pitch": SPEECH_PITCH,
        "loudness": 1.0,
        "enable_preprocessing": True,
        "speech_sample_rate": SAMPLE_RATE,
        "output_audio_codec": "wav",
    }
    async with session.post(
        SARVAM_TTS_URL,
        json=payload,
        headers=_sarvam_headers(),
        timeout=aiohttp.ClientTimeout(total=30),
    ) as response:
        body = await response.text()
        if response.status >= 400:
            raise RuntimeError(f"Sarvam speech failed ({response.status}): {body[:300]}")
        data = json.loads(body)
    return b"".join(wav_to_pcm(base64.b64decode(item)) for item in data.get("audios") or [])


class SpeechOut:
    """Stream Sarvam speech onto the call as soon as the first audio arrives."""

    def __init__(self) -> None:
        self.session: aiohttp.ClientSession | None = None
        self.socket: aiohttp.ClientWebSocketResponse | None = None

    async def connect(self) -> None:
        await self.close()
        self.session = aiohttp.ClientSession()
        self.socket = await self.session.ws_connect(
            SARVAM_TTS_WS,
            headers={"api-subscription-key": os.environ["SARVAM_API_KEY"]},
            heartbeat=20,
        )

    async def close(self) -> None:
        if self.socket is not None and not self.socket.closed:
            await self.socket.close()
        if self.session is not None and not self.session.closed:
            await self.session.close()
        self.socket = None
        self.session = None

    async def stream(self, text: str, language: str):
        if self.socket is None or self.socket.closed:
            await self.connect()
        code, speaker = voice_for(language)
        socket = self.socket
        assert socket is not None
        await socket.send_json(
            {
                "type": "config",
                "data": {
                    "target_language_code": code,
                    "speaker": speaker,
                    "pace": SPEECH_PACE,
                    "pitch": SPEECH_PITCH,
                    "speech_sample_rate": SAMPLE_RATE,
                    "output_audio_codec": "linear16",
                    "enable_preprocessing": True,
                },
            }
        )
        await socket.send_json({"type": "text", "data": {"text": text}})
        await socket.send_json({"type": "flush"})
        pending = PcmBuffer()
        while True:
            message = await socket.receive()
            if message.type != aiohttp.WSMsgType.TEXT:
                if message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR}:
                    for piece in pending.finish():
                        yield piece
                    return
                continue
            try:
                event = json.loads(message.data)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind == "audio":
                raw = base64.b64decode((event.get("data") or {}).get("audio") or "")
                for piece in pending.push(wav_to_pcm(raw)):
                    yield piece
                continue
            if kind == "event" and (event.get("data") or {}).get("event_type") == "final":
                for piece in pending.finish():
                    yield piece
                return
            if kind == "error":
                detail = (event.get("data") or {}).get("message") or event
                raise RuntimeError(f"Sarvam speech stream failed: {detail}")


def _is_echo(text: str) -> bool:
    folded = text.lower()
    return "namaste" in folded or "how may i help" in folded


def _sarvam_headers() -> dict[str, str]:
    key = os.environ.get("SARVAM_API_KEY", "")
    if not key:
        raise RuntimeError("SARVAM_API_KEY is missing")
    return {"api-subscription-key": key, "Content-Type": "application/json"}


def _stt_url() -> str:
    query = urlencode(
        {
            "language_code": "auto",
            "stream_type": "fast",
            "endpointing": "vad",
            "encoding": "linear16",
            "sample_rate": str(SAMPLE_RATE),
            "model": "saaras:v3-realtime",
            "mode": "transcribe",
            "prompt": (
                "mandi Kudlu Singasandra Kolar Huskur Bengaluru "
                "tomato onion banana mango grapes paddy ragi chilli maize"
            ),
            "return_timestamps": "false",
            "silence_duration_ms": "280",
        }
    )
    return f"{SARVAM_STT_URL}?{query}"


async def _pump_caller_audio(call: ExotelCall, incoming: asyncio.Queue[bytes | None]) -> None:
    session = aiohttp.ClientSession()
    try:
        socket = await session.ws_connect(
            _stt_url(),
            headers={"API-SUBSCRIPTION-KEY": os.environ["SARVAM_API_KEY"]},
            heartbeat=20,
        )
        call.stt = socket

        async def read_stt() -> None:
            async for message in socket:
                if message.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    event = json.loads(message.data)
                except json.JSONDecodeError:
                    continue
                await call.on_stt(event)

        reader = asyncio.create_task(read_stt())
        while True:
            audio = await incoming.get()
            if audio is None or socket.closed:
                break
            await socket.send_bytes(audio)
        await socket.close()
        reader.cancel()
    except Exception:
        logger.exception("speech recognition stopped")
    finally:
        await session.close()


async def handle_media(request: web.Request) -> web.WebSocketResponse:
    socket = web.WebSocketResponse()
    await socket.prepare(request)
    db: Database = request.app["db"]
    call = ExotelCall(socket, db)
    call.http = request.app["http"]
    call.cached = request.app["cached"]
    call.speech = SpeechOut()
    asyncio.create_task(call.speech.connect())
    incoming: asyncio.Queue[bytes | None] = asyncio.Queue()
    pump = asyncio.create_task(_pump_caller_audio(call, incoming))
    greeted = False
    try:
        async for message in socket:
            if message.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                event = json.loads(message.data)
            except json.JSONDecodeError:
                continue
            kind = event.get("event")
            if kind == "start":
                start = event.get("start") or {}
                call.stream_sid = str(start.get("stream_sid") or event.get("stream_sid") or "")
                call.call_sid = str(start.get("call_sid") or "")
                call.phone = normalize_phone(str(start.get("from") or ""))
                logger.info("exotel call from %s", call.phone or "unknown")
                if not greeted:
                    greeted = True
                    asyncio.create_task(call.greet())
            elif kind == "media":
                payload = ((event.get("media") or {}).get("payload")) or ""
                if payload:
                    await incoming.put(base64.b64decode(payload))
            elif kind == "stop":
                break
    finally:
        call._cancel_reply()
        await incoming.put(None)
        pump.cancel()
        if call.speech is not None:
            await call.speech.close()
        call.save()
        if not socket.closed:
            await socket.close()
    return socket


async def handle_voicebot(request: web.Request) -> web.Response:
    forwarded = request.headers.get("X-Forwarded-Proto", request.scheme)
    ws_scheme = "wss" if forwarded == "https" else "ws"
    host = request.headers.get("Host", f"127.0.0.1:{request.url.port}")
    return web.json_response({"url": f"{ws_scheme}://{host}/exotel/media"})


async def _open_clients(app: web.Application) -> None:
    app["http"] = aiohttp.ClientSession()
    cached: dict[str, bytes] = {}
    lines = {"greet-en": GREETING["en"]}
    for key, text in lines.items():
        language = key.split("-", 1)[1]
        try:
            cached[key] = await synthesize(text, language)
            logger.info("cached %s (%s bytes)", key, len(cached[key]))
        except Exception:
            logger.exception("could not cache %s", key)
    app["cached"] = cached


async def _close_clients(app: web.Application) -> None:
    session: aiohttp.ClientSession = app["http"]
    if not session.closed:
        await session.close()


def build_app() -> web.Application:
    db = Database()
    db.seed_if_empty()
    app = web.Application()
    app["db"] = db
    app["cached"] = {}
    app.on_startup.append(_open_clients)
    app.on_cleanup.append(_close_clients)
    app.router.add_get("/health", lambda _request: web.json_response({"ok": True}))
    app.router.add_get("/exotel/voicebot", handle_voicebot)
    app.router.add_get("/exotel/media", handle_media)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    port = int(os.environ.get("EXOTEL_PORT", "8788"))
    web.run_app(build_app(), host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
