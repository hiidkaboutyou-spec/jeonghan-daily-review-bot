from __future__ import annotations

import unittest

from app.channel_style_runtime import analyze_source, verify_hard_facts


class ChannelIdentityBoundaryTests(unittest.TestCase):
    def test_jeonghan_persian_does_not_invent_jun(self):
        source = "Jeonghan describes beauty for the September issue."
        output = "جونگهان برای شماره سپتامبر درباره زیبایی حرف می‌زند."
        issues = verify_hard_facts(source, output, analyze_source(source))
        self.assertNotIn("invented name/identity: JUN", issues)
        self.assertNotIn("name/identity dropped: JEONGHAN", issues)

    def test_real_persian_jun_is_still_detected_as_invented(self):
        source = "Jeonghan attended the event."
        output = "جونگهان و جون در رویداد حضور داشتند."
        issues = verify_hard_facts(source, output, analyze_source(source))
        self.assertIn("invented name/identity: JUN", issues)

    def test_latin_jun_requires_token_boundary(self):
        source = "Jeonghan posted an update in June."
        output = "Jeonghan posted an update in June."
        issues = verify_hard_facts(source, output, analyze_source(source))
        self.assertNotIn("invented name/identity: JUN", issues)

    def test_korean_particle_keeps_identity_detection(self):
        source = "준이 오늘 왔어요."
        output = "준이 امروز اومد."
        issues = verify_hard_facts(source, output, analyze_source(source))
        self.assertNotIn("name/identity dropped: JUN", issues)


if __name__ == "__main__":
    unittest.main()
