# -*- coding: utf-8 -*-
"""Tests for the minimal AlphaSift screening endpoints."""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

from api.v1.endpoints import alphasift as alphasift_endpoint
from src.config import Config

DEFAULT_ALPHASIFT_TEST_SPEC = (
    "git+https://github.com/ZhuLinsen/alphasift.git"
    "@2c76b2b6074ae3bae01d52e5e830a4af3e3246b2"
)


def _alphasift_unavailable() -> HTTPException:
    return HTTPException(
        status_code=424,
        detail={"error": "alphasift_unavailable", "message": "AlphaSift is unavailable"},
    )


def _raise_alphasift_unavailable() -> None:
    raise _alphasift_unavailable()


class AlphaSiftOpportunitiesApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        Config.reset_instance()

    def tearDown(self) -> None:
        Config.reset_instance()

    def _config(self, *, enabled: bool, install_spec: str = DEFAULT_ALPHASIFT_TEST_SPEC) -> Config:
        return Config(alphasift_enabled=enabled, alphasift_install_spec=install_spec)

    def _screen(self, config: Config, **kwargs):
        return alphasift_endpoint.alphasift_screen(
            alphasift_endpoint.AlphaSiftScreenRequest(**kwargs),
            config=config,
        )

    def test_status_defaults_to_disabled(self) -> None:
        config = self._config(enabled=False)

        with patch("api.v1.endpoints.alphasift._is_alphasift_available", return_value=False):
            payload = alphasift_endpoint.alphasift_status(config=config)

        self.assertEqual(payload["enabled"], False)
        self.assertEqual(payload["install_spec"], DEFAULT_ALPHASIFT_TEST_SPEC)

    def test_screen_rejects_when_disabled(self) -> None:
        config = self._config(enabled=False)

        with self.assertRaises(HTTPException) as caught:
            self._screen(config)

        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.detail["error"], "alphasift_disabled")

    def test_screen_reports_alphasift_install_failure(self) -> None:
        config = self._config(enabled=True)
        completed = SimpleNamespace(returncode=1, stdout="", stderr="not found")

        with (
            patch("api.v1.endpoints.alphasift.subprocess.run", return_value=completed),
            patch("api.v1.endpoints.alphasift._import_alphasift", side_effect=_raise_alphasift_unavailable),
        ):
            with self.assertRaises(HTTPException) as caught:
                self._screen(config)

        self.assertEqual(caught.exception.status_code, 424)
        payload = caught.exception.detail
        self.assertEqual(payload["error"], "alphasift_install_failed")
        self.assertIn("AlphaSift", payload["message"])

    def test_screen_rejects_pypi_placeholder_without_running_pip(self) -> None:
        config = self._config(enabled=True, install_spec="alphasift")

        with (
            patch("api.v1.endpoints.alphasift.subprocess.run") as run_mock,
            patch("api.v1.endpoints.alphasift._import_alphasift", side_effect=_raise_alphasift_unavailable),
        ):
            with self.assertRaises(HTTPException) as caught:
                self._screen(config)

        self.assertEqual(caught.exception.status_code, 424)
        self.assertEqual(caught.exception.detail["error"], "alphasift_install_spec_missing")
        run_mock.assert_not_called()

    def test_screen_rejects_untrusted_install_spec_without_running_pip(self) -> None:
        config = self._config(enabled=True, install_spec="git+https://example.com/evil/alphasift.git")

        with (
            patch("api.v1.endpoints.alphasift.subprocess.run") as run_mock,
            patch("api.v1.endpoints.alphasift._import_alphasift", side_effect=_raise_alphasift_unavailable),
        ):
            with self.assertRaises(HTTPException) as caught:
                self._screen(config)

        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.detail["error"], "alphasift_install_spec_not_allowed")
        run_mock.assert_not_called()

    def test_screen_auto_installs_alphasift_package_when_enabled(self) -> None:
        config = self._config(enabled=True)
        fake_module = SimpleNamespace(
            screen=MagicMock(return_value=[{"code": "600519", "name": "Kweichow Moutai", "score": 88.5}])
        )
        completed = SimpleNamespace(returncode=0, stdout="installed", stderr="")

        with (
            patch("api.v1.endpoints.alphasift.subprocess.run", return_value=completed) as run_mock,
            patch(
                "api.v1.endpoints.alphasift._import_alphasift",
                side_effect=[
                    _alphasift_unavailable(),
                    _alphasift_unavailable(),
                    fake_module,
                    fake_module,
                ],
            ),
        ):
            payload = self._screen(config, market="cn", strategy="dual_low", max_results=5)

        self.assertEqual(payload["candidate_count"], 1)
        run_mock.assert_called_once()

    def test_install_rejects_when_disabled_without_side_effects(self) -> None:
        config = self._config(enabled=False)

        with (
            patch("api.v1.endpoints.alphasift.subprocess.run") as run_mock,
            patch("api.v1.endpoints.alphasift._import_alphasift") as import_mock,
        ):
            with self.assertRaises(HTTPException) as caught:
                alphasift_endpoint.alphasift_install(config=config)

        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.detail["error"], "alphasift_disabled")
        import_mock.assert_not_called()
        run_mock.assert_not_called()

    def test_install_invokes_pip_when_enabled_and_missing(self) -> None:
        config = self._config(enabled=True)
        fake_module = SimpleNamespace(screen=MagicMock())
        completed = SimpleNamespace(returncode=0, stdout="installed", stderr="")

        with (
            patch("api.v1.endpoints.alphasift.subprocess.run", return_value=completed) as run_mock,
            patch(
                "api.v1.endpoints.alphasift._import_alphasift",
                side_effect=[_alphasift_unavailable(), fake_module],
            ),
        ):
            payload = alphasift_endpoint.alphasift_install(config=config)

        self.assertEqual(payload["installed"], True)
        self.assertEqual(payload["already_installed"], False)
        run_mock.assert_called_once()
        self.assertIn(DEFAULT_ALPHASIFT_TEST_SPEC, run_mock.call_args.args[0])

    def test_screen_calls_alphasift_package_when_enabled(self) -> None:
        config = self._config(enabled=True)
        fake_module = SimpleNamespace(
            screen=MagicMock(return_value=[{"code": "600519", "name": "Kweichow Moutai", "score": 88.5}])
        )

        with patch("api.v1.endpoints.alphasift._import_alphasift", return_value=fake_module):
            payload = self._screen(config, market="cn", strategy="dual_low", max_results=5)

        fake_module.screen.assert_called_once_with(
            "dual_low",
            market="cn",
            max_output=5,
            use_llm=False,
        )
        self.assertEqual(payload["candidate_count"], 1)
        self.assertEqual(payload["candidates"][0]["code"], "600519")


if __name__ == "__main__":
    unittest.main()
