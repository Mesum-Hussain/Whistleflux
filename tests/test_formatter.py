"""The clean-up pipeline: basic rules first, then S1-mini; nothing is sent anywhere."""

import unittest
from unittest import mock

from whistleflux import formatter, s1mini


def _cfg(basic=True, enabled=True) -> dict:
    return {"speech": {"language": ""}, "format": {"basic_cleanup": basic, "enabled": enabled, "timeout": 5},
            "s1_mini": {}}


class PipelineTest(unittest.TestCase):
    def test_rules_then_s1_mini(self):
        with mock.patch.object(s1mini, "format_text", return_value="Clean.") as local:
            self.assertEqual(formatter.format_text("um see you soon", _cfg()), "Clean.")
        self.assertEqual(local.call_args.args[0], "See you soon")  # fillers removed first

    def test_disabled_skips_s1_mini(self):
        with mock.patch.object(s1mini, "format_text") as local:
            self.assertEqual(formatter.format_text("um hello", _cfg(enabled=False)), "Hello")
        local.assert_not_called()

    def test_filler_only_never_reaches_s1_mini(self):
        with mock.patch.object(s1mini, "format_text") as local:
            self.assertEqual(formatter.format_text("um uh", _cfg()), "")
        local.assert_not_called()

    def test_basic_cleanup_off(self):
        with mock.patch.object(s1mini, "format_text", side_effect=lambda t, c: t) as local:
            self.assertEqual(formatter.format_text("um hello", _cfg(basic=False)), "um hello")
        local.assert_called_once()

    def test_no_network(self):
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used")), \
                mock.patch.object(s1mini, "is_downloaded", return_value=False):
            self.assertEqual(formatter.format_text("um hello there", _cfg()), "Hello there")


if __name__ == "__main__":
    unittest.main()
