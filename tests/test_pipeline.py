"""Speech, summary, and dashboard price formatting. Prices are not stored locally."""

from __future__ import annotations

import importlib.util
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from kisan.advice import _is_closing, should_answer
from kisan.language import detect_language, spoken_text, voice_for
from kisan.audio import CHUNK_BYTES, PcmBuffer, audio_chunks, wav_to_pcm
from kisan.summary import extract_turns, overlap_score, render_summary


class PipelineTests(unittest.TestCase):
    def test_language_switch_picks_the_kannada_voice(self) -> None:
        self.assertEqual(detect_language("ಬೆಲೆ ಎಷ್ಟು"), "kn")
        self.assertEqual(detect_language("मंडी का भाव"), "hi")
        self.assertEqual(detect_language("what is the price", hint="kn-IN"), "kn")
        self.assertEqual(detect_language("Tomato", current="kn"), "kn")
        self.assertEqual(detect_language("ಇವತ್ತು ಟೊಮೆಟೊ ಬೆಲೆ ಎಷ್ಟು"), "kn")
        self.assertEqual(voice_for("kn")[0], "kn-IN")
        self.assertEqual(voice_for("hi")[1], "shreya_hi_conversational")
        self.assertNotIn("customer", voice_for("en")[1])
        hindi = spoken_text("नमस्ते. भाव 1,800 है. बारिश नहीं है।", "hi")
        self.assertEqual(hindi, "नमस्ते। भाव 1,800 है। बारिश नहीं है।")
        self.assertEqual(spoken_text("Namaste! How may I help you?", "en"), "Namaste. How may I help you?")
        self.assertEqual(spoken_text("Yes! Sell there!", "en"), "Sell there.")
        self.assertEqual(spoken_text("Yesterday the price fell.", "en"), "Yesterday the price fell.")
        self.assertEqual(spoken_text("Namaste, how can I help?", "en"), "Namaste, how can I help?")

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

    def test_closing_and_noise(self) -> None:
        self.assertTrue(_is_closing("no nothing thank you"))
        self.assertTrue(_is_closing("thank you"))
        self.assertFalse(_is_closing("What is the tomato price in Kolar?"))
        self.assertTrue(should_answer("Hello"))
        self.assertFalse(should_answer("uh"))

    def test_gemini_parse_keeps_the_spoken_line(self) -> None:
        from kisan.gemini_reply import known_place, parse_gemini_reply

        say, place, end_call = parse_gemini_reply(
            '{"say": "Tomato in Kolar is 1,800 rupees a quintal.", "save_place": "Kolar", "end_call": false}'
        )
        self.assertIn("1,800", say)
        self.assertEqual(known_place(place), "Kolar")
        self.assertFalse(end_call)

    def test_prices_come_from_the_dashboard_only(self) -> None:
        from kisan.raitha import DEFAULT_URL, board_url, live_facts
        import inspect

        self.assertIsNone(importlib.util.find_spec("kisan.db"))
        source = inspect.getsource(live_facts)
        self.assertIn("snapshots", source)
        self.assertNotIn("sqlite", source)
        self.assertNotIn("kisan.db", source)
        with patch.dict(os.environ, {"RAITHA_API_URL": ""}):
            self.assertEqual(board_url(), DEFAULT_URL)

    def test_caller_memory_is_postgres_and_keeps_numbers_apart(self) -> None:
        import inspect

        from kisan.memory import Memory, format_memory

        source = inspect.getsource(Memory)
        self.assertIn("psycopg", inspect.getsource(__import__("kisan.memory", fromlist=["memory"])))
        self.assertNotIn("sqlite", source.lower())
        self.assertNotIn("mandi_price", source)
        self.assertIn("WHERE phone = %s", inspect.getsource(Memory.recent_calls))
        self.assertIn("WHERE phone = %s", inspect.getsource(Memory.get_farmer))
        phone = "+919812345678"
        first = format_memory(phone, None, [])
        self.assertIn("first stored call", first)
        second = format_memory(
            phone,
            {"district": "Kolar", "crop": "tomato"},
            [{
                "ended_at": "2026-11-17 10:00",
                "summary": "Farmer asked: tomato price in Kolar. Agent answered: 1800 rupees per quintal.",
            }],
        )
        self.assertIn("1800 rupees", second)
        self.assertIn("Kolar", second)
        self.assertIn(phone, second)
        self.assertNotIn("first stored call", second)
        other = format_memory("+919800000001", None, [])
        self.assertIn("first stored call", other)
        self.assertNotIn("1800 rupees", other)

    def test_live_board_states_the_modal_for_the_asked_crop(self) -> None:
        from kisan.raitha import format_market_facts

        boards = [
            {
                "crop": "potato",
                "mandi": "Hassan APMC",
                "district": "Hassan",
                "as_of": "2026-11-16",
                "modal": 3999,
                "min_price": 3999,
                "max_price": 3999,
                "arrival_7d_tonnes": 315.5,
                "shortage": None,
                "forecast_7d": None,
                "action_label": "No advice",
            },
            {
                "crop": "maize",
                "mandi": "Davangere APMC",
                "district": "Davangere",
                "as_of": "2026-11-17",
                "modal": 3236,
                "arrival_7d_tonnes": 2057.6,
                "seasonal_arrival_7d_tonnes": 5602.3,
                "shortage": True,
                "action_label": "Sell now",
                "action_reason": "The price is above last year and arrivals are short.",
            },
            {
                "crop": "tomato",
                "mandi": "Kolar APMC",
                "district": "Kolar",
                "as_of": "2026-11-16",
                "modal": 1200,
                "min_price": 300,
                "max_price": 2330,
                "forecast_7d": 1152.4,
                "nearby": {
                    "mandi": "Binny Mill Bengaluru APMC",
                    "modal": 1700,
                    "distance_km": 70,
                    "transport_rs_per_qtl": 140,
                    "worth_the_trip": True,
                },
                "action_label": "Sell nearby",
                "action_reason": "The nearby mandi covers the transport.",
            },
        ]
        facts = format_market_facts(boards, "", None, ["potato", "maize"])
        self.assertIn("potato at Hassan APMC, Hassan, reported 2026-11-16.", facts)
        self.assertIn("Modal 3999.", facts)
        self.assertIn("shortage unknown", facts)
        self.assertIn("Supply: arrivals 2058 tonnes this week, usual 5602 tonnes, shortage yes.", facts)
        self.assertIn("tomato at Kolar APMC: modal 1200, reported 2026-11-16.", facts)
        self.assertNotIn("The trip is worth it", facts)
        self.assertNotIn("1800", facts)

    def test_board_answers_when_the_model_is_busy(self) -> None:
        from kisan import raitha

        raitha._CACHE[:] = [
            {
                "crop": "maize",
                "district": "Davangere",
                "modal": 3236,
                "shortage": True,
            },
            {
                "crop": "tomato",
                "district": "Kolar",
                "modal": 1200,
                "shortage": False,
                "nearby": {"mandi": "Binny Mill Bengaluru APMC", "modal": 1700, "distance_km": 70, "worth_the_trip": True},
            },
        ]
        maize = raitha.speak_board("ದಾವಣಗೆರೆಯಲ್ಲಿ ಜೋಳ ಬೆಲೆ ಒಳ್ಳೆಯ.", "kn")
        self.assertIn("3236", maize)
        self.assertIn("ಸರಕು ಕಡಿಮೆ", maize)
        tomato = raitha.speak_board("ಟೊಮೆಟೊ ಕೋಲಾರದಲ್ಲಿ ಮಾರಬೇಕಾ ಅಥವಾ?", "kn")
        self.assertIn("1700", tomato)
        self.assertIn("ಅಲ್ಲಿಗೆ ಹೋಗಿ", tomato)
        self.assertEqual(raitha.speak_board("ಇದು", "kn"), "")
        sell = raitha.speak_board("I want to sell tomatoes.", "en")
        self.assertIn("1700", sell)
        self.assertIn("Go there", sell)
        hindi = raitha.speak_board("Mujhe tamatar bechna hai", "hi")
        self.assertIn("1700", hindi)
        self.assertIn("बेचिए", hindi)
        self.assertEqual(detect_language("Mujhe tamatar bechna hai"), "hi")
        raitha._CACHE.clear()


if __name__ == "__main__":
    unittest.main()
