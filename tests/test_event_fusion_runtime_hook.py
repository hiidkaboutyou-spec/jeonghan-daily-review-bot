from __future__ import annotations

import unittest

from app.media_delivery_runtime import MediaDedupReviewApplication
from app.private_runtime import PrivateReviewApplication


class EventFusionRuntimeHookTests(unittest.TestCase):
    def test_private_review_delivery_has_shadow_event_hook(self):
        self.assertTrue(
            getattr(
                PrivateReviewApplication.deliver_updates,
                "_event_fusion_private_shadow_installed",
                False,
            )
        )

    def test_production_media_review_runtime_inherits_shadow_hook(self):
        self.assertIs(
            MediaDedupReviewApplication.deliver_updates,
            PrivateReviewApplication.deliver_updates,
        )


if __name__ == "__main__":
    unittest.main()
