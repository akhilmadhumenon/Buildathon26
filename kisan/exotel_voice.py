"""Exotel Voicebot websocket. Callers of 02241642353 reach this instead of a forwarded mobile."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
from urllib.parse import urlencode

import aiohttp
from aiohttp import web
from dotenv import load_dotenv

from kisan.advice import GOODBYE, _is_closing, _mentioned, should_answer
from kisan.gemini_reply import gemini_reply
from kisan.raitha import board_url, snapshots, speak_board
from kisan.sarvam_reply import sarvam_reply
from kisan.names import correct_transcript
from kisan.audio import CHUNK_BYTES, PcmBuffer, audio_chunks, wav_to_pcm
from kisan.memory import Memory, NO_MEMORY, now_ist
from kisan.reference import CROP_ALIASES, Caller, normalize_phone
from kisan.language import SPEECH_PACE, SPEECH_PITCH, SPEECH_TAIL, detect_language, spoken_text, voice_for

load_dotenv()

logger = logging.getLogger("kisan.exotel")

SAMPLE_RATE = 8000
SARVAM_STT_URL = "wss://api.sarvam.ai/speech-to-text-realtime/ws"
SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
SARVAM_TTS_WS = "wss://api.sarvam.ai/text-to-speech/ws?model=bulbul:v4-flash&send_completion_event=true"

GREETING = {
    "en": "Namaste, how can I help?",
    "hi": "नमस्ते, क्या मदद करूँ?",
    "kn": "ನಮಸ್ಕಾರ, ಹೇಗೆ ಸಹಾಯ ಮಾಡಲಿ?",
}

RETRY = {
    "en": "Sorry, please say that once more.",
    "hi": "माफ़ कीजिए, एक बार फिर बोलिए।",
    "kn": "ಕ್ಷಮಿಸಿ, ಇನ್ನೊಮ್ಮೆ ಹೇಳಿ.",
}

MARKET_DOWN = {
    "en": "I could not reach the market prices. Please ask once more.",
    "hi": "मंडी भाव अभी नहीं मिल रहे। एक बार फिर पूछिए।",
    "kn": "ಮಂಡಿ ಬೆಲೆ ಈಗ ಸಿಗಲಿಲ್ಲ. ಇನ್ನೊಮ್ಮೆ ಕೇಳಿ.",
}

def sale_opening(language: str, reason: str) -> str:
    """Automated calls start with hello and the desk's reason, then the yes or no."""
    content = " ".join((reason or "Your crop is ready to sell.").split())
    if language == "hi":
        return f"Hello. {content} क्या आप बेचेंगे? हाँ या नहीं बोलिए।"
    if language == "kn":
        return f"Hello. {content} ಮಾರಾಟ ಮಾಡುವಿರಾ? ಹೌದು ಅಥವಾ ಇಲ್ಲ ಅನ್ನಿ."
    return f"Hello. {content} Will you sell it? Please say yes or no."

SALE_AGAIN = {
    "en": "Please say yes or no.",
    "hi": "हाँ या नहीं बोलिए।",
    "kn": "ಹೌದು ಅಥವಾ ಇಲ್ಲ ಅನ್ನಿ.",
}

SALE_HEARD = {
    "yes": {
        "en": "Thank you. I have marked yes on the desk.",
        "hi": "धन्यवाद। डेस्क पर हाँ लिख दिया है।",
        "kn": "ಧನ್ಯವಾದ. ಡೆಸ್ಕ್ ಮೇಲೆ ಹೌದು ಎಂದು ಬರೆದಿದ್ದೇನೆ.",
    },
    "no": {
        "en": "Thank you. I have marked no on the desk.",
        "hi": "धन्यवाद। डेस्क पर नहीं लिख दिया है।",
        "kn": "ಧನ್ಯವಾದ. ಡೆಸ್ಕ್ ಮೇಲೆ ಇಲ್ಲ ಎಂದು ಬರೆದಿದ್ದೇನೆ.",
    },
}

SALE_MISSED = {
    "en": "I heard you, but the desk did not save it. Please say yes or no once more.",
    "hi": "मैंने सुना, पर डेस्क पर नहीं लिखा। एक बार फिर हाँ या नहीं बोलिए।",
    "kn": "ನಾನು ಕೇಳಿದೆ, ಆದರೆ ಡೆಸ್ಕ್ ಉಳಿಸಲಿಲ್ಲ. ಇನ್ನೊಮ್ಮೆ ಹೌದು ಅಥವಾ ಇಲ್ಲ ಅನ್ನಿ.",
}

_YES_WORD = re.compile(r"\b(yes|yeah|yep|yup|haan|hanji)\b", re.IGNORECASE)
_NO_WORD = re.compile(r"\b(no|nope|nah|nahi|nahin)\b", re.IGNORECASE)
_YES_SCRIPT = ("हाँ", "हां", "ಹೌದು")
_NO_SCRIPT = ("नहीं", "नही", "ಇಲ್ಲ", "ಬೇಡ")
_PENDING: dict[str, dict] = {}


def _sale_echo(text: str) -> bool:
    """The phone hears its own hello. That is not the farmer's yes or no."""
    folded = " ".join(text.casefold().split()).strip(" .,!?")
    if folded in {"hello", "hi", "hey", "okay", "ok", "yeah"}:
        return True
    return any(
        phrase in folded
        for phrase in ("yes or no", "will you sell", "ready to sell", "shortage", "please say")
    )


def sale_choice(text: str) -> str | None:
    folded = text.casefold()
    yes_at = -1
    no_at = -1
    for word in _YES_SCRIPT:
        at = text.find(word)
        if at >= 0:
            yes_at = max(yes_at, at)
    for word in _NO_SCRIPT:
        at = text.find(word)
        if at >= 0:
            no_at = max(no_at, at)
    for match in _YES_WORD.finditer(folded):
        yes_at = max(yes_at, match.start())
    for match in _NO_WORD.finditer(folded):
        no_at = max(no_at, match.start())
    if (yes_at < 0 and no_at < 0) or (yes_at >= 0 and no_at >= 0):
        return None
    return "yes" if yes_at >= 0 else "no"


def _remember_sale(phone: str, record: dict) -> None:
    key = normalize_phone(phone)
    if key:
        _PENDING[key] = record


def _forget_sale(phone: str) -> None:
    key = normalize_phone(phone)
    if key:
        _PENDING.pop(key, None)


async def _store_sale(memory: Memory | None, phone: str, record: dict) -> None:
    _remember_sale(phone, record)
    if memory is None or not memory.ready:
        return
    key = normalize_phone(phone)
    if not key:
        return
    trigger = record.get("trigger_id")
    try:
        trigger_id = int(trigger) if trigger is not None and str(trigger).isdigit() else None
    except (TypeError, ValueError):
        trigger_id = None
    try:
        await asyncio.to_thread(
            memory.save_sale,
            key,
            trigger_id=trigger_id,
            language=str(record.get("language") or "en"),
            reason=str(record.get("reason") or ""),
            crop=str(record.get("crop") or "") or None,
            district=str(record.get("district") or "") or None,
        )
    except Exception:
        logger.exception("could not store the sale call")


async def _drop_sale(memory: Memory | None, phone: str) -> None:
    _forget_sale(phone)
    if memory is None or not memory.ready:
        return
    key = normalize_phone(phone)
    if not key:
        return
    try:
        await asyncio.to_thread(memory.clear_sale, key)
    except Exception:
        logger.exception("could not clear the sale call")


async def _pending_sale(memory: Memory | None, phone: str, other: str = "") -> dict | None:
    keys = [normalize_phone(phone), normalize_phone(other)]
    for key in keys:
        if key and key in _PENDING:
            return _PENDING[key]
    if memory is not None and memory.ready:
        for key in keys:
            if not key:
                continue
            try:
                found = await asyncio.to_thread(memory.load_sale, key)
            except Exception:
                logger.exception("could not load the sale call")
                found = None
            if found:
                return found
        try:
            found = await asyncio.to_thread(memory.latest_sale)
        except Exception:
            logger.exception("could not load the latest sale call")
            found = None
        if found:
            return found
    if len(_PENDING) == 1:
        return next(iter(_PENDING.values()))
    return None


class ExotelCall:
    def __init__(self, socket: web.WebSocketResponse) -> None:
        self.socket = socket
        self.caller = Caller()
        self.memory: Memory | None = None
        self.started_at = now_ist()
        self._saved_place = ""
        self._stored = False
        self.phone = ""
        self.other_phone = ""
        self.call_sid = ""
        self._ending = False
        self.stream_sid = ""
        self.language = "en"
        self.confirm: dict | None = None
        self._sale_done = False
        self._opening = False
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
        if self._opening:
            return
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
        pcm += b"\x00" * int(SAMPLE_RATE * SPEECH_TAIL) * 2
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
        pending = await _pending_sale(self.memory, self.phone, self.other_phone)
        if pending:
            self.confirm = pending
            language = pending.get("language") or "en"
            self.language = language if language in {"en", "hi", "kn"} else "en"
            logger.info("sale greeting for %s", self.phone or "unknown")
            self._opening = True
            self._held = None
            try:
                await self.speak(sale_opening(self.language, str(pending.get("reason") or "")))
            finally:
                self._ignore_until = max(self._ignore_until, time.perf_counter()) + 1.0
                self._held = None
                self._cancel_task(self._reply_task)
                self._cancel_task(self._partial_task)
                self._opening = False
            return
        logger.info("generic greeting for %s", self.phone or "unknown")
        self.language = "en"
        await self.play_cached(GREETING["en"], "greet-en")

    async def _report_sale(self, choice: str) -> bool:
        pending = self.confirm or {}
        trigger_id = pending.get("trigger_id")
        if not trigger_id or self.http is None:
            return False
        url = f"{board_url()}/v1/admin/triggers/{trigger_id}/answer"
        try:
            async with self.http.post(
                url,
                json={"answer": choice},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as response:
                if response.status >= 400:
                    logger.warning("desk answer returned %s", response.status)
                    return False
        except Exception:
            logger.exception("desk answer failed")
            return False
        await _drop_sale(self.memory, self.phone)
        if self.other_phone:
            await _drop_sale(self.memory, self.other_phone)
        return True

    async def reply_to(self, text: str, language_hint: str | None, final: bool = True) -> None:
        if self.confirm and not self._sale_done:
            if not final:
                return
            self.turns.append(("user", text))
            choice = sale_choice(text)
            if choice is None:
                await self.speak(SALE_AGAIN.get(self.language, SALE_AGAIN["en"]))
                return
            if not await self._report_sale(choice):
                await self.speak(SALE_MISSED.get(self.language, SALE_MISSED["en"]))
                return
            self._sale_done = True
            await self.speak(SALE_HEARD[choice].get(self.language, SALE_HEARD[choice]["en"]))
            await self.end_call()
            return
        started = time.perf_counter()
        self.language = detect_language(text, language_hint, current=self.language)
        logger.info("language %s", self.language)
        self.turns.append(("user", text))
        try:
            answer, end_call = await answer_caller(self.http, self.caller, self.turns, self.language)
        except asyncio.TimeoutError:
            logger.warning("chat timed out for %s", self.phone)
            answer, end_call = RETRY.get(self.language, RETRY["en"]), False
        except Exception:
            logger.exception("reply failed")
            answer, end_call = RETRY.get(self.language, RETRY["en"]), False
        self._remember_place()
        if not answer:
            logger.info("no answer for %s", text[:80])
            return
        self.language = detect_language(answer, current=self.language)
        logger.info("answer ready in %.2fs: %s", time.perf_counter() - started, answer[:160])
        await self.speak(answer)
        if end_call or answer in GOODBYE.values():
            await self.end_call()

    def _remember_place(self) -> None:
        place = self.caller.place
        memory = self.memory
        if not place or place == self._saved_place or memory is None or not memory.ready or not self.phone:
            return
        self._saved_place = place
        asyncio.create_task(self._save_place(place))

    async def _save_place(self, place: str) -> None:
        memory = self.memory
        if memory is None:
            return
        try:
            await asyncio.to_thread(memory.save_farmer, self.phone, district=place)
        except Exception:
            logger.exception("could not save caller place")

    def _cancel_task(self, task: asyncio.Task[None] | None) -> None:
        if task and not task.done():
            task.cancel()

    def _cancel_reply(self) -> None:
        self._cancel_task(self._reply_task)
        self._cancel_task(self._partial_task)
        self._cancel_task(self._hold_task)

    def _worth_hearing(self, transcript: str) -> bool:
        if self.confirm and not self._sale_done and sale_choice(transcript):
            return True
        return should_answer(transcript)

    def _begin_reply(self, transcript: str, hint: str, final: bool = True) -> None:
        transcript = correct_transcript(transcript)
        if self._opening or (self.confirm and not self._sale_done and _sale_echo(transcript)):
            logger.info("ignored sale echo %s", transcript[:80])
            return
        if _is_echo(transcript):
            logger.info("ignored echo %s", transcript[:80])
            return
        if not self._worth_hearing(transcript) or transcript == self._last_heard:
            if transcript != self._last_heard:
                logger.info("ignored %s", transcript[:80])
            return
        logger.info("heard %s", transcript[:120])
        self._last_heard = transcript
        replacing = self._reply_task is not None and not self._reply_task.done()
        self._cancel_task(self._reply_task)
        self._reply_task = asyncio.create_task(self._reply_after_clear(transcript, hint, replacing, final))

    async def _reply_after_clear(self, transcript: str, hint: str, replacing: bool, final: bool = True) -> None:
        if replacing:
            await self.clear_playback()
        await self.reply_to(transcript, hint, final)

    def _hold(self, transcript: str, hint: str) -> None:
        transcript = correct_transcript(transcript)
        if _is_echo(transcript) or not self._worth_hearing(transcript):
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
        if _is_echo(transcript) or not self._worth_hearing(transcript):
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
        self._begin_reply(transcript, hint, final=False)

    async def on_stt(self, event: dict) -> None:
        if self._ending or self._opening:
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


async def answer_caller(
    http: aiohttp.ClientSession | None,
    caller: Caller,
    turns: list[tuple[str, str]],
    language: str = "en",
) -> tuple[str, bool]:
    """Gemini writes the reply. Sarvam writes it from the same board when Gemini cannot."""
    question = turns[-1][1] if turns and turns[-1][0] == "user" else ""
    heard = await gemini_reply(http, caller, turns)
    if not heard:
        logger.info("gemini unavailable, asking sarvam")
        heard = await sarvam_reply(http, caller, turns)
    if heard:
        say, place, end_call = heard
        if place:
            caller.place = place
        return say, end_call
    if _is_closing(question):
        return GOODBYE.get(language, GOODBYE["en"]), True
    if http is not None:
        try:
            await snapshots(http)
        except Exception:
            logger.exception("raitha board failed")
    spoken = speak_board(question, language)
    if spoken:
        return spoken, False
    if _mentioned(question, CROP_ALIASES):
        return MARKET_DOWN.get(language, MARKET_DOWN["en"]), False
    return RETRY.get(language, RETRY["en"]), False


async def synthesize(text: str, language: str) -> bytes:
    line = spoken_text(text, language)
    if not line:
        return b""
    async with aiohttp.ClientSession() as session:
        return await _synthesize_segment(session, line, language)


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
        await socket.send_json({"type": "text", "data": {"text": spoken_text(text, language) or text}})
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
    call = ExotelCall(socket)
    call.http = request.app["http"]
    call.cached = request.app["cached"]
    call.memory = request.app["memory"]
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
                call.other_phone = normalize_phone(str(start.get("to") or ""))
                call.caller.phone = call.phone
                call.started_at = now_ist()
                logger.info("exotel call from %s", call.phone or "unknown")
                await _load_memory(call)
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
        await _store_call(call)
        await incoming.put(None)
        pump.cancel()
        if call.speech is not None:
            await call.speech.close()
        if not socket.closed:
            await socket.close()
    return socket


async def handle_voicebot(request: web.Request) -> web.Response:
    forwarded = request.headers.get("X-Forwarded-Proto", request.scheme)
    ws_scheme = "wss" if forwarded == "https" else "ws"
    host = request.headers.get("Host", f"127.0.0.1:{request.url.port}")
    return web.json_response({"url": f"{ws_scheme}://{host}/exotel/media"})


async def _load_memory(call: ExotelCall) -> None:
    memory = call.memory
    if memory is None or not memory.ready or not call.phone:
        if not call.caller.memory:
            call.caller.memory = NO_MEMORY
        return
    try:
        await asyncio.to_thread(memory.load, call.caller)
    except Exception:
        logger.exception("could not load caller memory")
        call.caller.memory = NO_MEMORY
        return
    call._saved_place = call.caller.place


async def _store_call(call: ExotelCall) -> None:
    if call._stored:
        return
    call._stored = True
    memory = call.memory
    if memory is None or not memory.ready or not call.phone:
        return
    try:
        await asyncio.to_thread(
            memory.finish_call,
            call.caller,
            list(call.turns),
            call.language,
            call.started_at,
        )
    except Exception:
        logger.exception("could not store caller memory")


async def _open_clients(app: web.Application) -> None:
    logger.info("mandi prices from %s", board_url())
    try:
        await asyncio.to_thread(app["memory"].init)
    except Exception:
        logger.exception("caller memory unavailable")
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


def _exotel_from(phone: str) -> str:
    digits = "".join(char for char in phone if char.isdigit())
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if len(digits) != 10:
        return ""
    return "0" + digits


async def handle_dial(request: web.Request) -> web.Response:
    """Call this farmer and land them on the same Voicebot applet."""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    caller = _exotel_from(str(body.get("phone") or ""))
    if not caller:
        return web.json_response({"error": "phone"}, status=400)
    language = str(body.get("language") or "en")
    if language not in {"en", "hi", "kn"}:
        language = "en"
    record = {
        "trigger_id": body.get("trigger_id"),
        "language": language,
        "reason": str(body.get("reason") or ""),
        "crop": str(body.get("crop") or "maize"),
        "district": str(body.get("district") or ""),
    }
    await _store_sale(request.app["memory"], caller, record)
    sid = os.environ.get("EXOTEL_ACCOUNT_SID", "")
    key = os.environ.get("EXOTEL_API_KEY", "")
    token = os.environ.get("EXOTEL_API_TOKEN", "")
    host = os.environ.get("EXOTEL_SUBDOMAIN", "api.exotel.com")
    exophone = os.environ.get("EXOTEL_EXOPHONE", "")
    flow = os.environ.get("EXOTEL_VOICE_URL", "")
    if not (sid and key and token and exophone and flow):
        await _drop_sale(request.app["memory"], caller)
        return web.json_response({"error": "exotel"}, status=503)
    url = f"https://{host}/v1/Accounts/{sid}/Calls/connect.json"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                data={"From": caller, "CallerId": exophone, "Url": flow, "CallType": "trans"},
                auth=aiohttp.BasicAuth(key, token),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                text = await response.text()
                if response.status >= 400:
                    logger.warning("exotel dial returned %s", response.status)
                    await _drop_sale(request.app["memory"], caller)
                    return web.json_response({"error": text[:180]}, status=502)
    except Exception:
        logger.exception("exotel dial failed")
        await _drop_sale(request.app["memory"], caller)
        return web.json_response({"error": "exotel"}, status=502)
    logger.info("dialed %s", caller)
    return web.json_response({"ok": True, "phone": caller})


def build_app() -> web.Application:
    app = web.Application()
    app["memory"] = Memory()
    app["cached"] = {}
    app.on_startup.append(_open_clients)
    app.on_cleanup.append(_close_clients)
    app.router.add_get("/health", lambda _request: web.json_response({"ok": True}))
    app.router.add_get("/exotel/voicebot", handle_voicebot)
    app.router.add_post("/exotel/dial", handle_dial)
    app.router.add_get("/exotel/media", handle_media)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Locally ngrok forwards to 127.0.0.1:8788. Railway sets PORT and connects from outside the container.
    port = int(os.environ.get("PORT") or os.environ.get("EXOTEL_PORT") or "8788")
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    web.run_app(build_app(), host=host, port=port)


if __name__ == "__main__":
    main()
