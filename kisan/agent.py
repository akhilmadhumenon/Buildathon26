"""Inbound voice agent. One process answers browser calls and SIP phone calls."""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    WorkerOptions,
    cli,
    function_tool,
)

from kisan.advice import format_caller_context, harvest_answer, mandi_answer, related_calls
from kisan.caller import resolve_phone
from kisan.db import Database, now_iso
from kisan.language import SPEECH_PACE, SPEECH_PITCH, detect_language, voice_for
from kisan.summary import extract_turns, render_summary

load_dotenv()

logger = logging.getLogger("kisan")

INSTRUCTIONS = """
You are Kisan Sahayak, a phone helper for farmers in Karnataka.
Sound as glad and sure as the greeting "Namaste!". Begin a price with "Yes!" and end it with an exclamation.
Never sound doubtful, skeptical, or suspicious. Never hedge with "still", "I think", or "maybe".
Use one or two short sentences. Never use lists, markdown, or symbols.
In Hindi and Kannada, write in that script.
The caller may speak Kannada, Hindi, or English, and may switch in the middle of the call.
Always answer in the language of the caller's latest sentence.
Use the caller's words for crop names when you answer, and use the database facts for numbers.

Caller memory and profile are below. Mention an earlier call only when this question is about the same crop and the same place or harvest. If it is a different question, ignore the notes.

For a price question, call get_mandi_price. Do not invent a price.
For a harvest question, call get_harvest_advice. If district, crop, or sowing date is missing, ask for the missing piece before you advise.
When the caller tells you their name, district, crop, or sowing date, call save_farmer_profile.
Prices and crop durations in the database are demo figures for this pilot.

{context}
""".strip()


class KisanAgent(Agent):
    def __init__(self, *, phone: str, context: str, db: Database) -> None:
        super().__init__(instructions=INSTRUCTIONS.format(context=context))
        self.phone = phone
        self.db = db
        self.language = "en"

    @function_tool
    async def get_mandi_price(self, crop: str, mandi: str) -> str:
        """Look up the latest mandi price and recent change for a crop.

        Args:
            crop: Crop name, such as tomato, ragi, maize, paddy, onion, or chilli.
            mandi: Mandi or district, such as Kolar, Mandya, Davangere, Hubballi, or Gangavathi.
        """
        answer = mandi_answer(self.db, crop, mandi)
        logger.info("mandi lookup crop=%s place=%s", crop, mandi)
        return answer

    @function_tool
    async def get_harvest_advice(self, crop: str, district: str, sown_on: str) -> str:
        """Decide whether a crop is ready to harvest from the sowing date, crop calendar, and rain.

        Args:
            crop: Crop the farmer grew.
            district: District or mandi town the field is in.
            sown_on: Sowing date as YYYY-MM-DD.
        """
        self.db.save_farmer(self.phone, crop=crop, district=district, sown_on=sown_on)
        answer = harvest_answer(self.db, crop=crop, district=district, sown_on=sown_on)
        logger.info("harvest advice crop=%s district=%s sown=%s", crop, district, sown_on)
        return answer

    @function_tool
    async def save_farmer_profile(
        self,
        district: str = "",
        crop: str = "",
        sown_on: str = "",
        name: str = "",
    ) -> str:
        """Store the farmer's name, district, crop, or sowing date against this phone number.

        Args:
            district: District or town. Leave empty if the caller did not say it.
            crop: Crop name. Leave empty if the caller did not say it.
            sown_on: Sowing date as YYYY-MM-DD. Leave empty if unknown.
            name: Farmer's name. Leave empty if unknown.
        """
        record = self.db.save_farmer(
            self.phone,
            name=name or None,
            district=district or None,
            crop=crop or None,
            sown_on=sown_on or None,
        )
        return (
            "Saved for this phone number. "
            f"District {record.get('district') or 'unknown'}, "
            f"crop {record.get('crop') or 'unknown'}, "
            f"sown on {record.get('sown_on') or 'unknown'}."
        )

    @function_tool
    async def recall_previous_calls(self, question: str) -> str:
        """Find earlier calls from this same phone number that match the farmer's question.

        Args:
            question: The question the farmer just asked, in their own words.
        """
        return related_calls(self.db, self.phone, question)


def _history_items(session: AgentSession, agent: KisanAgent) -> list[object]:
    history = getattr(session, "history", None)
    if history is not None:
        items = getattr(history, "items", None)
        if items:
            return list(items)
    chat_items = getattr(agent.chat_ctx, "items", None)
    return list(chat_items or [])


async def entrypoint(ctx: JobContext) -> None:
    started_at = now_iso()
    db = Database()
    db.seed_if_empty()

    await ctx.connect()
    participant = await ctx.wait_for_participant()
    phone = resolve_phone(ctx, participant)
    if not phone:
        phone = "unknown"
        logger.warning("caller id missing; this call will not match a later call")
    context = format_caller_context(db, phone)
    logger.info("call from %s", phone)

    agent = KisanAgent(phone=phone, context=context, db=db)
    session = AgentSession(
        stt=_build_stt(),
        llm=_build_llm(),
        tts=_build_tts("en"),
        vad=None,
        turn_handling={
            "turn_detection": "stt",
            "endpointing": {"mode": "fixed", "min_delay": 0.4, "max_delay": 3.0},
            "interruption": {
                "enabled": True,
                "mode": "vad",
                "min_words": 1,
                "false_interruption_timeout": 1.5,
                "resume_false_interruption": True,
            },
            "preemptive_generation": {"enabled": True},
        },
        max_tool_steps=6,
        user_away_timeout=None,
        aec_warmup_duration=None
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
        else 3.0,
    )

    @session.on("user_input_transcribed")
    def _on_transcript(event) -> None:
        if not getattr(event, "is_final", True):
            return
        transcript = getattr(event, "transcript", "") or ""
        language = detect_language(transcript, getattr(event, "language", None))
        if language == agent.language:
            return
        agent.language = language
        code, speaker = voice_for(language)
        if session.tts is not None:
            session.tts.update_options(target_language_code=code, speaker=speaker)
        logger.info("language switched to %s", code)

    saved = False

    async def _save_summary(_reason: str = "") -> None:
        nonlocal saved
        if saved:
            return
        saved = True
        turns = extract_turns(_history_items(session, agent))
        summary, questions, answers = render_summary(turns)
        if phone == "unknown":
            logger.warning("skipping summary save because the caller number is unknown")
            return
        call_id = db.append_call(
            phone,
            summary=summary,
            questions=questions,
            answers=answers,
            language=agent.language,
            started_at=started_at,
        )
        logger.info("saved call %s for %s", call_id, phone)

    ctx.add_shutdown_callback(_save_summary)
    await session.start(agent=agent, room=ctx.room)

    greeting = 'Greet the caller with exactly this cheerful line, and then wait: "Namaste! How may I help you?"'
    await session.generate_reply(instructions=greeting)


def _build_stt():
    from livekit.plugins import sarvam

    return sarvam.STTRealtime(
        language="auto",
        stream_type="fast",
        mode="transcribe",
        endpointing="vad",
        sample_rate=16000,
        vad_min_silence_ms=550,
    )


def _build_tts(language: str):
    from livekit.plugins import sarvam

    code, speaker = voice_for(language)
    return sarvam.TTS(
        model="bulbul:v4-flash",
        speaker=speaker,
        target_language_code=code,
        pace=SPEECH_PACE,
        pitch=SPEECH_PITCH,
        enable_preprocessing=True,
        min_buffer_size=30,
        max_chunk_length=150,
        output_audio_codec="linear16",
        speech_sample_rate=24000,
    )


def _build_llm():
    from livekit.plugins import sarvam

    return sarvam.LLM(
        model=os.environ.get("SARVAM_LLM_MODEL", "sarvam-105b"),
        temperature=0.3,
        reasoning_effort="low",
        max_tokens=350,
    )


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=os.environ.get("AGENT_NAME", "kisan-agent"),
        )
    )
