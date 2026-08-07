from __future__ import annotations

import unittest
from unittest.mock import Mock

from app.telegram import TelegramBot


class FinalStabilityTests(unittest.TestCase):
    def test_callback_ack_is_bounded_and_single_attempt(self):
        bot = TelegramBot("fake", 1, -100)
        bot.api = Mock(return_value=True)

        bot.answer_callback("callback-1", "در حال انجام…")

        bot.api.assert_called_once_with(
            "answerCallbackQuery",
            data={"callback_query_id": "callback-1", "text": "در حال انجام…"},
            timeout=8,
            attempts=1,
        )


if __name__ == "__main__":
    unittest.main()
