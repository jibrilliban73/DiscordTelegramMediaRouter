import json
import os
import unittest
from unittest.mock import patch

from media_router import discord_media_url, load_routing_config, media_attachments, resolve_channel_route


class SourceTests(unittest.TestCase):
    photo = "https://cdn.discordapp.com/attachments/123/456/photo.JPG?ex=abc&is=def&hm=signature"
    video = "https://media.discordapp.net/attachments/123/789/clip.mp4?width=960"

    def test_direct_discord_photo_and_video_urls_are_media(self):
        message = {"content": f"<{self.photo}>\n[clip]({self.video})"}
        items = media_attachments(message)
        self.assertEqual([i["_kind"] for i in items], ["photo", "video"])
        self.assertEqual(items[0]["url"], self.photo)
        self.assertEqual(items[1]["filename"], "clip.mp4")

    def test_ordinary_links_and_website_previews_are_ignored(self):
        message = {
            "content": "https://example.com/photo.jpg https://discord.com/channels/1/2/3 https://youtu.be/example",
            "embeds": [{"url": "https://example.com", "thumbnail": {"url": self.photo}, "image": {"url": "https://media.discordapp.net/external/proxy/photo.jpg"}}],
        }
        self.assertEqual(media_attachments(message), [])

    def test_discord_media_in_an_image_embed_is_included(self):
        self.assertEqual(media_attachments({"embeds": [{"image": {"url": self.photo}}]})[0]["_kind"], "photo")

    def test_attachment_link_and_embed_of_same_file_are_not_duplicated(self):
        items = media_attachments({"attachments": [{"filename": "photo.JPG", "url": self.photo}], "content": self.photo, "embeds": [{"image": {"url": self.photo.replace("cdn.discordapp.com", "media.discordapp.net")}}]})
        self.assertEqual(len(items), 1)

    def test_non_media_and_lookalike_discord_hosts_are_rejected(self):
        for url in (
            "https://cdn.discordapp.com.evil.test/attachments/123/456/a.png",
            "https://cdn.discordapp.com@evil.test/attachments/123/456/a.png",
            "http://cdn.discordapp.com/attachments/123/456/a.png",
            "https://cdn.discordapp.com/attachments/123/456/a.txt",
            "https://media.discordapp.net/external/proxy/a.png",
            "https://cdn.discordapp.com:999/attachments/123/456/a.png",
        ):
            with self.subTest(url=url):
                self.assertIsNone(discord_media_url(url))

    def test_explicit_id_override_beats_a_different_channel_name(self):
        topics = {"20": {"name": "Archive"}, "30": {"name": "News"}}
        self.assertEqual(resolve_channel_route({"id": "123", "name": "News"}, topics, {"123": "20"})[0], "20")
        self.assertEqual(resolve_channel_route({"id": "123", "name": "Renamed"}, topics, {"123": "20"})[0], "20")

    def test_missing_explicit_target_never_falls_back_to_another_topic(self):
        self.assertIsNone(resolve_channel_route({"id": "123", "name": "News"}, {"30": {"name": "News"}}, {"123": "20"})[0])

    def test_ordinary_names_still_match_when_no_override_exists(self):
        self.assertEqual(resolve_channel_route({"id": "123", "name": "Queens"}, {"20": {"name": "Queens 👸"}, "30": {"name": "Tech Queens"}}, {})[0], "20")

    def test_private_config_validation(self):
        config = {"topics": {"20": {"name": "Archive"}}, "channel_overrides": {"123": "20"}}
        with patch.dict(os.environ, {"ROUTER_CONFIG_JSON": json.dumps(config)}):
            self.assertEqual(load_routing_config(), config)
        for invalid in ("not-json", "[]", '{"topics":[]}', '{"topics":{"20":{"name":""}}}', '{"channel_overrides":{"123":20}}'):
            with self.subTest(config=invalid), patch.dict(os.environ, {"ROUTER_CONFIG_JSON": invalid}):
                with self.assertRaises(RuntimeError):
                    load_routing_config()


if __name__ == "__main__":
    unittest.main()
