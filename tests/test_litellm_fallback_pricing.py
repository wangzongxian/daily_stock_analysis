# -*- coding: utf-8 -*-
"""Tests for fallback LiteLLM pricing registration."""

import unittest
from unittest.mock import patch

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    from tests.litellm_stub import ensure_litellm_stub

    ensure_litellm_stub()

from src.agent import llm_adapter


class LiteLLMFallbackPricingTestCase(unittest.TestCase):
    def test_register_fallback_pricing_registers_openai_model(self) -> None:
        registered = []

        def _register(payload):
            registered.append(payload)

        with patch.object(llm_adapter.litellm, "register_model", side_effect=_register, create=True):
            with patch.object(llm_adapter.litellm, "model_cost", {}, create=True):
                llm_adapter._FALLBACK_MODEL_PRICING_REGISTERED.clear()
                llm_adapter.register_fallback_model_pricing(["openai/mimo-alpha"])

        self.assertTrue(any("mimo-alpha" in payload for payload in registered))
