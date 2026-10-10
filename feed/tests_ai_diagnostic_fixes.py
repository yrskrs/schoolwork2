"""
Tests for stability and resilience improvements based on the AI diagnostics report:
- HTTP 413 mitigation (Token counting, Groq limit preflight, theory truncation)
- HTTP 400 mitigation (Gemini payload sanitization, MIME whitelist, fallback without thinking/json_mode)
- JSON Parsing error resilience (repair_json_string balancing and retry)
- External resilience (Cooldown 60s on 503, Gemini timeout 50s)
"""
import json
from unittest.mock import patch
from django.test import TestCase
from feed.gemini_service import repair_json_string, extract_json_from_text, _raw_call_ai_api
from feed.ai_model_catalog import get_model_capabilities


class AIDiagnosticFixesTests(TestCase):
    def test_repair_json_string_truncated_array(self):
        truncated = '{"suggested_grade": "10", "summary": "Чудова робота", "criteria_results": [{"criterion": "Оформлення", "status": "completed"'
        res = repair_json_string(truncated)
        self.assertIsNotNone(res)
        self.assertEqual(res.get("suggested_grade"), "10")
        self.assertEqual(res.get("summary"), "Чудова робота")
        self.assertIn("criteria_results", res)

    def test_repair_json_string_truncated_inside_string(self):
        truncated = '{"suggested_grade": "9", "feedback_comment": "Учень гарно постарався над завданням, проте не вистачи'
        res = repair_json_string(truncated)
        self.assertIsNotNone(res)
        self.assertEqual(res.get("suggested_grade"), "9")
        self.assertTrue(res.get("feedback_comment").startswith("Учень гарно"))

    def test_repair_json_string_dangling_commas_and_escapes(self):
        broken = '{"suggested_grade": "11", "strengths": ["граматика", "охайність",], "summary": "Тест (\'приклад\')",}'
        res = repair_json_string(broken)
        self.assertIsNotNone(res)
        self.assertEqual(res.get("suggested_grade"), "11")
        self.assertEqual(len(res.get("strengths", [])), 2)

    def test_repair_json_string_markdown_fences(self):
        text = '```json\n{"suggested_grade": "12", "summary": "Відмінний результат"}\n```'
        res = repair_json_string(text)
        self.assertIsNotNone(res)
        self.assertEqual(res.get("suggested_grade"), "12")

    def test_extract_json_from_text_rejects_partial_assessment_in_strict_mode(self):
        truncated = '{"suggested_grade": "8", "summary": "Короткий зміст", "tasks_completed_count": 1'
        # A recovered suggestion is useful for display, but cannot make an incomplete
        # assessment authoritative. Strict evaluation must request a complete reply.
        res = extract_json_from_text(truncated, allow_partial=False)
        self.assertIsNone(res)
        self.assertEqual(extract_json_from_text(truncated).get("suggested_grade"), "8")

    def test_gemini_payload_sanitization(self):
        posted_payloads = []

        def fake_post(url, payload, headers, timeout):
            posted_payloads.append((payload, timeout))
            return 200, {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}, '{"candidates": []}'

        valid_png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAA="
        with patch("feed.gemini_service._http_post_json", side_effect=fake_post), \
             patch("feed.ai_payload.optimize_media", side_effect=lambda media, provider: media):
            # Test empty prompt_text -> parts must not be empty
            # Test unsupported mime_type (e.g., application/zip) -> must not be sent in inlineData
            # Test timeout <= 35 -> gemini gets at least 50
            inline_media = [
                {"mime_type": "application/zip", "data": "UEsDBB...", "source": "archive.zip"},
                {"mime_type": "image/png", "data": f"data:image/png;base64,{valid_png}", "source": "photo.png"},
            ]
            status_code, reply, err, data = _raw_call_ai_api(
                prompt_text="   ",
                system_prompt="  Вчительська інструкція  ",
                inline_media=inline_media,
                provider="gemini",
                api_key="test-key",
                model_name="gemini-2.5-flash",
                timeout=35,
            )

            self.assertEqual(status_code, 200)
            self.assertEqual(len(posted_payloads), 1)
            payload, timeout = posted_payloads[0]
            self.assertGreaterEqual(timeout, 50)

            # System instruction trimmed
            self.assertEqual(payload["systemInstruction"]["parts"][0]["text"], "Вчительська інструкція")

            # Parts verified
            parts = payload["contents"][0]["parts"]
            self.assertGreater(len(parts), 0)

            # Zip was filtered out of inlineData
            inline_mimes = [p["inlineData"]["mimeType"] for p in parts if "inlineData" in p]
            self.assertIn("image/png", inline_mimes)
            self.assertNotIn("application/zip", inline_mimes)

            # base64 prefix was stripped
            png_part = [p for p in parts if p.get("inlineData", {}).get("mimeType") == "image/png"][0]
            self.assertFalse(png_part["inlineData"]["data"].startswith("data:"))

    def test_gemini_fallback_on_400_invalid_argument(self):
        call_count = [0]
        posted_configs = []

        def fake_post(url, payload, headers, timeout):
            call_count[0] += 1
            posted_configs.append(dict(payload.get("generationConfig", {})))
            if call_count[0] == 1:
                # First call returns 400 because of thinkingConfig
                return 400, None, '{"error": {"message": "Invalid argument: thinkingConfig not supported"}}'
            return 200, {"candidates": [{"content": {"parts": [{"text": '{"suggested_grade": "10"}'}]}}]}, '{"ok": true}'

        with patch("feed.gemini_service._http_post_json", side_effect=fake_post):
            status_code, reply, err, data = _raw_call_ai_api(
                prompt_text="Оціни",
                provider="gemini",
                api_key="test-key",
                model_name="gemini-2.5-flash",
                thinking_budget=1024,
            )
            self.assertEqual(status_code, 200)
            self.assertEqual(call_count[0], 2)
            # thinkingConfig was present in first attempt, removed in retry
            self.assertIn("thinkingConfig", posted_configs[0])
            self.assertNotIn("thinkingConfig", posted_configs[1])

    def test_groq_model_capabilities_and_limits(self):
        caps = get_model_capabilities("llama-3.3-70b-versatile", "groq")
        self.assertLessEqual(caps["context_tokens"], 7000)
        self.assertEqual(caps.get("max_images"), 0)

        qwen_caps = get_model_capabilities("qwen/qwen3.8-27b", "groq")
        self.assertLessEqual(qwen_caps["context_tokens"], 7000)
        self.assertEqual(qwen_caps.get("max_images"), 3)
