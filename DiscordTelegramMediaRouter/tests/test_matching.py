import unittest
from media_router import match_topic, media_attachments


class RoutingTests(unittest.TestCase):
    topics = {
        "100": {"name": "WTJ"},
        "200": {"name": "Sports"},
        "300": {"name": "News"},
    }

    def test_exact(self):
        self.assertEqual(match_topic("wtj", self.topics)[0], "100")

    def test_platform_ignoring(self):
        self.assertEqual(match_topic("WTJ Discord", self.topics)[0], "100")
        self.assertEqual(match_topic("Sports Videos", self.topics)[0], "200")
        self.assertEqual(match_topic("NEWS-Telegram", self.topics)[0], "300")

    def test_no_unsafe_guesses(self):
        self.assertIsNone(match_topic("finance", self.topics)[0])
        self.assertIsNone(match_topic("WTJ", {"100": {"name": "WTJ Photos"}, "101": {"name": "WTJ Videos"}})[0])

    def test_empty_or_emoji_only_names_are_not_a_match(self):
        self.assertIsNone(match_topic("✨", {"100": {"name": "🎉"}})[0])
        self.assertIsNone(match_topic("", self.topics)[0])

    def test_media(self):
        message = {"attachments": [
            {"filename": "a.png", "content_type": "image/png"},
            {"filename": "b.mp4", "content_type": "video/mp4"},
            {"filename": "c.txt", "content_type": "text/plain"},
        ]}
        self.assertEqual([a["_kind"] for a in media_attachments(message)], ["photo", "video"])


if __name__ == "__main__":
    unittest.main()
