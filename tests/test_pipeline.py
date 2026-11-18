"""The memory loop the phone agent uses: first call writes a summary, the next call reads it."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from types import SimpleNamespace

from kisan.advice import (
    facts_for_question,
    format_caller_context,
    harvest_answer,
    mandi_answer,
    related_calls,
    should_answer,
    spoken_mandi,
)
from kisan.caller import resolve_phone
from kisan.db import Database
from kisan.language import detect_language, speech_segments, voice_for
from kisan.audio import CHUNK_BYTES, PcmBuffer, audio_chunks, wav_to_pcm
from kisan.summary import extract_turns, overlap_score, render_summary


class MemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self._dir.name, "kisan.db"))
        self.db.seed_if_empty()
        self.phone = "+919812345678"

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_phone_numbers_from_the_same_handset_match(self) -> None:
        ctx = SimpleNamespace(job=SimpleNamespace(metadata=""), room=SimpleNamespace(metadata=""))
        participant = SimpleNamespace(
            attributes={"sip.phoneNumber": "09812345678"},
            metadata="",
        )
        os.environ.pop("DEMO_CALLER_PHONE", None)
        self.assertEqual(resolve_phone(ctx, participant), self.phone)

    def test_second_call_sees_the_first_summary(self) -> None:
        first = format_caller_context(self.db, self.phone)
        self.assertIn("first call", first)

        self.db.append_call(
            self.phone,
            summary="Farmer asked: tomato price in Kolar. Agent answered: 1800 rupees per quintal, trend down.",
            questions="what is the tomato price in Kolar",
            answers="1800 rupees per quintal and the price is falling",
            language="en",
            started_at="2026-11-17T10:00:00+05:30",
        )
        second = format_caller_context(self.db, self.phone)
        self.assertIn("1800 rupees", second)
        self.assertIn(self.phone, second)

        recalled = related_calls(self.db, self.phone, "tomato price in Kolar again")
        self.assertIn("1800 rupees", recalled)
        self.assertIn("Closest earlier call", recalled)

    def test_another_number_does_not_see_the_summary(self) -> None:
        self.db.append_call(
            self.phone,
            summary="private note",
            questions="tomato",
            answers="1800",
            language="hi",
            started_at="2026-11-17T10:00:00+05:30",
        )
        other = format_caller_context(self.db, "+919800000001")
        self.assertIn("first call", other)
        self.assertNotIn("private note", other)

    def test_mandi_and_harvest_come_from_the_database(self) -> None:
        price = mandi_answer(self.db, "tomatoes", "kolar")
        self.assertIn("1800 rupees per quintal", price)
        self.assertIn("down", price)

        advice = harvest_answer(
            self.db,
            crop="ragi",
            district="Mandya",
            sown_on="2026-08-01",
            today=date(2026, 11, 17),
            weather_lookup=lambda district: "No heavy rain is expected.",
        )
        self.assertIn("too early", advice)
        self.assertIn("No heavy rain", advice)

    def test_language_switch_picks_the_kannada_voice(self) -> None:
        self.assertEqual(detect_language("ಬೆಲೆ ಎಷ್ಟು"), "kn")
        self.assertEqual(detect_language("मंडी का भाव"), "hi")
        self.assertEqual(detect_language("what is the price", hint="kn-IN"), "kn")
        self.assertEqual(detect_language("Tomato", current="kn"), "kn")
        self.assertEqual(detect_language("ಇವತ್ತು ಟೊಮೆಟೊ ಬೆಲೆ ಎಷ್ಟು"), "kn")
        self.assertEqual(voice_for("kn")[0], "kn-IN")
        self.assertEqual(voice_for("hi")[1], "shreya_hi_conversational")
        self.assertNotIn("customer", voice_for("en")[1])
        segments = speech_segments("नमस्ते. भाव 1,800 है. बारिश नहीं है।", "hi")
        self.assertEqual(segments[0], "नमस्ते।")
        self.assertIn("1,800", segments[1])

    def test_summary_keeps_the_question_and_the_answer(self) -> None:
        turns = extract_turns(
            [
                SimpleNamespace(role="assistant", text_content="Namaskara. How can I help?"),
                SimpleNamespace(role="user", text_content="Can I harvest my ragi?"),
                SimpleNamespace(role="assistant", text_content="Not yet. About twelve days are left."),
            ]
        )
        summary, questions, answers = render_summary(turns)
        self.assertIn("Can I harvest my ragi?", questions)
        self.assertIn("twelve days", answers)
        self.assertGreater(overlap_score("harvest my ragi", summary), 0)

    def test_phone_audio_chunks_match_exotel_sizes(self) -> None:
        header = b"RIFF" + b"\x00" * 4 + b"WAVE" + b"data" + (4).to_bytes(4, "little") + b"\x01\x02\x03\x04"
        pcm = wav_to_pcm(header)
        self.assertEqual(pcm, b"\x01\x02\x03\x04")
        chunks = audio_chunks(pcm)
        self.assertTrue(chunks)
        self.assertTrue(all(len(chunk) == 3200 and len(chunk) % 320 == 0 for chunk in chunks))
        buffer = PcmBuffer()
        self.assertEqual(buffer.push(b"\x00" * (CHUNK_BYTES + 10)), [b"\x00" * CHUNK_BYTES])
        self.assertEqual(len(buffer.finish()[0]), CHUNK_BYTES)

    def test_named_crop_and_mandi_skip_a_tool_round(self) -> None:
        fact = facts_for_question(self.db, "What is the tomato price in Kolar?")
        self.assertIn("1800", fact)
        hindi = facts_for_question(self.db, "कोलार में टमाटर का भाव क्या है")
        self.assertIn("1800", hindi)
        self.assertIn("1,800", spoken_mandi(self.db, "tomato price in Kolar", "en"))
        rice = spoken_mandi(
            self.db,
            "Is it a right time to sell my rice harvest in Kolar mandi?",
            "en",
        )
        self.assertIn("2,320", rice)
        self.assertIn("2,530", rice)
        self.assertIn("Gangavathi", rice)
        self.assertIn("Wait a week", rice)
        tomato = spoken_mandi(self.db, "Should I sell my tomato in Kolar now, or wait a week?", "en")
        self.assertIn("1,800", tomato)
        self.assertIn("1,100", tomato)
        self.assertIn("Sell at Kolar now", tomato)
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO mandi_prices
                    (crop, mandi, district, price_per_quintal, recorded_on, trend)
                VALUES ('tomato', 'Bengaluru', 'Bengaluru', 2500, '2026-11-17', 'up')
                """
            )
        nearer = spoken_mandi(self.db, "Should I sell my tomato in Kolar now, or wait a week?", "en")
        self.assertIn("2,500", nearer)
        self.assertIn("Sell at Bengaluru now", nearer)
        price = spoken_mandi(self.db, "What is the tomato price in Kolar?", "en", phone="+919800000001")
        self.assertIn("1,800", price)
        self.assertNotIn("calling from", price)
        sale = spoken_mandi(
            self.db,
            "Should I sell my rice harvest?",
            "en",
            phone="+919800000002",
        )
        self.assertIn("calling from", sale)
        self.assertNotIn("2,320", sale)
        followed = spoken_mandi(
            self.db,
            "Kolar",
            "en",
            phone="+919800000002",
            earlier=["Should I sell my rice harvest?"],
        )
        self.assertIn("Gangavathi", followed)
        self.assertIn("2,320", followed)
        self.assertEqual(self.db.get_farmer("+919800000002")["district"], "Kolar")
        kannada_sale = spoken_mandi(self.db, "ನಾನು ಭತ್ತ ಮಾರಾಟ ಮಾಡಬೇಕೇ?", "kn", phone="+919800000003")
        self.assertIn("ಎಲ್ಲಿಂದ", kannada_sale)
        kannada = spoken_mandi(self.db, "ಇವತ್ತು ಟೊಮೆಟೊ ಪ್ರೈಸ್ ಎಷ್ಟು ಕೋಲಾರ ಮಂಡಿಯಲ್ಲಿ?", "kn")
        self.assertIn("1,800", kannada)
        self.assertIn("ಹೌದು", kannada)
        self.assertEqual(spoken_mandi(self.db, "What is the price of tomato today?", "en"), "Of course! Which mandi?")
        self.assertIn("टमाटर", spoken_mandi(self.db, "कोलार में टमाटर का भाव", "hi"))
        self.assertIn("2,450", spoken_mandi(self.db, "onion", "en", earlier=["tomato price in Kolar"]))
        self.assertIn("2,450", spoken_mandi(self.db, "kolar", "en", earlier=["tomato price in Kolar", "onion"]))
        self.assertIn(
            "2,450",
            spoken_mandi(
                self.db,
                "onion",
                "kn",
                earlier=["ಇವತ್ತು ಟೊಮೆಟೊ ಬೆಲೆ ಎಷ್ಟು ಕೋಲಾರ ಮಂಡ್ಯದಲ್ಲಿ?"],
            ),
        )
        self.assertIn("1,800", spoken_mandi(self.db, "ಇವತ್ತು ಟೊಮೆಟೊ ಬೆಲೆ ಎಷ್ಟು ಕೋಲಾರ ಮಂಡ್ಯದಲ್ಲಿ?", "kn"))
        self.assertIn("nice day", spoken_mandi(self.db, "no nothing thank you", "en"))
        self.assertIn("nice day", spoken_mandi(self.db, "thank you", "en"))
        self.assertEqual(spoken_mandi(self.db, "Hello", "en"), "Yes! Go ahead.")
        self.assertTrue(should_answer("Hello"))
        saved = spoken_mandi(self.db, "Kolar", "en", phone=self.phone)
        self.assertIn("saved", saved)
        self.assertEqual(self.db.get_farmer(self.phone)["district"], "Kolar")
        nearest = spoken_mandi(self.db, "Which is the nearest mandi to me?", "en", phone=self.phone)
        self.assertIn("Kolar", nearest)
        self.assertIn("1,800", nearest)
        self.db.save_farmer(self.phone, district="Hassan")
        deal = spoken_mandi(self.db, "Where is the best price for onion?", "en", phone=self.phone)
        self.assertIn("Hubballi", deal)
        self.assertIn("2,450", deal)

    def test_gemini_sees_prices_and_writes_the_line(self) -> None:
        from kisan.advice import market_facts
        from kisan.gemini_reply import known_place, parse_gemini_reply

        facts = market_facts(self.db, "")
        self.assertIn("1800", facts)
        self.assertIn("2320", facts)
        self.assertIn("2530", facts)
        say, place, end_call = parse_gemini_reply(
            '{"say": "Yes! Tomato in Kolar is 1,800 rupees a quintal!", "save_place": "Kolar", "end_call": false}'
        )
        self.assertIn("1,800", say)
        self.assertEqual(known_place(place), "Kolar")
        self.assertFalse(end_call)
        self.assertEqual(facts_for_question(self.db, "what is the price"), "")


if __name__ == "__main__":
    unittest.main()
