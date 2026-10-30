import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

import media_router as router


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_path = patch.object(router, "STATE_FILE", Path(self.tmp.name) / "state.json")
        self.state_path.start()
        self.addCleanup(self.state_path.stop)
        self.token_env = patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test"})
        self.token_env.start()
        self.addCleanup(self.token_env.stop)
        self.state = router.default_state()

    def test_pagination_returns_every_message_in_order_and_cursor_for_resume(self):
        # An inclusive cursor lets a failed multi-attachment message resume.
        first = [{"id": str(n)} for n in range(250, 150, -1)]
        second = [{"id": str(n)} for n in range(150, 99, -1)]
        with patch.object(router, "discord_request", side_effect=[first, second]) as api:
            messages = router.get_new_messages("token", "channel", "100")
        self.assertEqual([int(m["id"]) for m in messages], list(range(100, 251)))
        self.assertEqual(api.call_args_list[1].args[2]["before"], "151")

    def test_partial_attachment_failure_retries_only_unsent_attachment(self):
        self.state["channels"]["10"] = cursor = {"last_message_id": "100", "last_attachment_index": 0}
        ch = {"id": "10", "name": "WTJ"}
        message = {"id": "101", "attachments": [
            {"filename": "photo.png", "url": "photo"},
            {"filename": "video.mp4", "url": "video"},
        ]}
        with patch.dict(os.environ, {"DISCORD_GUILD_ID": "987533334894772284"}), \
             patch.object(router, "get_new_messages", return_value=[message]), \
             patch.object(router, "send_media", side_effect=[None, RuntimeError("upload failed")]):
            with self.assertRaisesRegex(RuntimeError, "upload failed"):
                router.process_channel("d", "t", "group", self.state, ch, "20", cursor)
        self.assertEqual(router.load_state()["channels"]["10"], {"last_message_id": "101", "last_attachment_index": 1})
        with patch.dict(os.environ, {"DISCORD_GUILD_ID": "987533334894772284"}), \
             patch.object(router, "get_new_messages", return_value=[message]), \
             patch.object(router, "send_media") as send:
            router.process_channel("d", "t", "group", self.state, ch, "20", cursor)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.args[3]["filename"], "video.mp4")
        self.assertEqual(cursor["last_attachment_index"], 2)

    def test_first_channel_run_skips_history(self):
        latest = {"id": "100", "attachments": [{"filename": "old.png"}]}
        with patch.object(router, "discord_request", return_value=[latest]), \
             patch.object(router, "send_media") as send:
            router.process_channel("d", "t", "group", self.state, {"id": "10", "name": "WTJ"}, "20", None)
        send.assert_not_called()
        self.assertEqual(self.state["channels"]["10"], {"last_message_id": "100", "last_attachment_index": 1})

    def test_unmatched_channel_keeps_cursor(self):
        self.state["topics"] = {"20": {"name": "WTJ"}}
        self.state["channels"]["11"] = {"last_message_id": "100", "last_attachment_index": 1}
        channels = [{"id": "10", "name": "WTJ"}, {"id": "11", "name": "unmatched"}]
        with patch.object(router, "list_channels", return_value=channels), \
             patch.object(router, "process_channel", return_value=0) as process:
            router.process_discord("d", "guild", "t", "group", self.state)
        self.assertEqual(process.call_count, 1)
        self.assertEqual(self.state["channels"]["11"]["last_message_id"], "100")

    def test_no_matches_is_an_actionable_failure(self):
        self.state["topics"] = {"20": {"name": "WTJ"}}
        with patch.object(router, "list_channels", return_value=[{"id": "10", "name": "unmatched"}]), \
             patch.object(router, "process_channel") as process:
            with self.assertRaisesRegex(RuntimeError, "No Discord channels match"):
                router.process_discord("d", "guild", "t", "group", self.state)
        process.assert_not_called()
        self.assertEqual(self.state["channels"], {})

    def test_no_channels_fails(self):
        with patch.object(router, "list_channels", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "No Discord text channels"):
                router.process_discord("d", "guild", "t", "group", self.state)

    def test_all_matched_channels_forbidden_fails(self):
        self.state["topics"] = {"20": {"name": "WTJ"}}
        response = requests.Response()
        response.status_code = 403
        with patch.object(router, "list_channels", return_value=[{"id": "10", "name": "WTJ"}]), \
             patch.object(router, "process_channel", side_effect=requests.HTTPError(response=response)):
            with self.assertRaisesRegex(RuntimeError, "cannot read any matched channel"):
                router.process_discord("d", "guild", "t", "group", self.state)

    def test_owner_binding_and_other_users_cannot_change_route(self):
        updates = [
            {"update_id": 1, "message": {"chat": {"id": -100123}, "from": {"id": 42}, "message_thread_id": 20, "text": "/bind WTJ"}},
            {"update_id": 2, "message": {"chat": {"id": -100123}, "from": {"id": 99}, "message_thread_id": 20, "text": "/bind Other"}},
            {"update_id": 3, "message": {"chat": {"id": -100999}, "from": {"id": 42}, "message_thread_id": 30, "text": "/bind Other Group"}},
        ]
        def telegram(_token, method, **kwargs):
            return updates if method == "getUpdates" else {}
        with patch.object(router, "telegram", side_effect=telegram) as api:
            router.poll_registration_commands("token", "-100123", "42", self.state)
        self.assertEqual(self.state["topics"], {"20": {"name": "WTJ"}})
        self.assertEqual(self.state["telegram_offset"], 4)
        self.assertEqual(sum(c.args[1] == "sendMessage" for c in api.call_args_list), 1)

    def test_oversized_media_never_sends_a_replacement_link(self):
        with patch.object(router, "telegram") as api, patch.object(router, "download_media") as download:
            with self.assertRaises(router.MediaTooLarge):
                router.send_media("token", "group", "20", {"filename": "big.mp4", "size": router.MAX_BYTES + 1})
        download.assert_not_called()
        api.assert_not_called()

    def test_oversized_attachment_does_not_block_next_media_file(self):
        cursor = {"last_message_id": "100", "last_attachment_index": 0}
        self.state["channels"]["10"] = cursor
        message = {"id": "101", "attachments": [{"filename": "large.mp4"}, {"filename": "next.png"}]}
        with patch.object(router, "get_new_messages", return_value=[message]), \
             patch.object(router, "send_media", side_effect=[router.MediaTooLarge(), None]) as send:
            skipped = router.process_channel("d", "t", "group", self.state, {"id": "10", "name": "WTJ"}, "20", cursor)
        self.assertEqual(skipped, 1)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(cursor, {"last_message_id": "101", "last_attachment_index": 2})

    def test_state_is_encrypted_and_unchanged_state_is_not_rewritten(self):
        self.state["topics"] = {"20": {"name": "Private topic name"}}
        router.save_state(self.state)
        encrypted = router.STATE_FILE.read_bytes()
        self.assertNotIn(b"Private topic name", encrypted)
        self.assertEqual(router.load_state(), self.state)
        router.save_state(self.state)
        self.assertEqual(router.STATE_FILE.read_bytes(), encrypted)

    def test_wrong_bot_token_cannot_read_or_overwrite_state(self):
        router.save_state(self.state)
        encrypted = router.STATE_FILE.read_bytes()
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "different-token"}):
            with self.assertRaisesRegex(RuntimeError, "Cannot decrypt"):
                router.load_state()
            with self.assertRaisesRegex(RuntimeError, "Refusing to overwrite"):
                router.save_state(self.state)
        self.assertEqual(router.STATE_FILE.read_bytes(), encrypted)

    def test_photo_preview_rejection_falls_back_to_original_file(self):
        with patch.object(router, "download_media", return_value=b"image"), \
             patch.object(router, "telegram", side_effect=[router.TelegramHttpError(400, "invalid photo"), {}]) as api:
            router.send_media("token", "group", "20", {"filename": "photo.heic", "_kind": "photo", "content_type": "image/heic"})
        self.assertEqual([c.args[1] for c in api.call_args_list], ["sendPhoto", "sendDocument"])
        self.assertTrue(all(c.kwargs["data"]["message_thread_id"] == "20" for c in api.call_args_list))

    def test_missing_configuration_lists_all_names_without_values(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DISCORD_BOT_TOKEN.*TELEGRAM_BOT_TOKEN.*TELEGRAM_GROUP_ID.*TELEGRAM_OWNER_USER_ID"):
                router.main()

    def test_no_registered_topics_does_not_initialize_discord_cursors(self):
        env = {name: "test" for name in ("DISCORD_BOT_TOKEN", "DISCORD_GUILD_ID", "TELEGRAM_BOT_TOKEN", "TELEGRAM_GROUP_ID", "TELEGRAM_OWNER_USER_ID")}
        with patch.dict(os.environ, env), patch.object(router, "poll_registration_commands"), \
             patch.object(router, "process_discord") as process:
            with self.assertRaisesRegex(RuntimeError, "No Telegram topics registered"):
                router.main()
        process.assert_not_called()
        self.assertEqual(router.load_state()["channels"], {})

    def test_progress_is_saved_when_sync_fails(self):
        env = {name: "test" for name in ("DISCORD_BOT_TOKEN", "DISCORD_GUILD_ID", "TELEGRAM_BOT_TOKEN", "TELEGRAM_GROUP_ID", "TELEGRAM_OWNER_USER_ID")}
        def poll(*args):
            args[-1]["telegram_offset"] = 12
            args[-1]["topics"] = {"20": {"name": "WTJ"}}
        with patch.dict(os.environ, env), patch.object(router, "poll_registration_commands", side_effect=poll), \
             patch.object(router, "process_discord", side_effect=RuntimeError("failed")):
            with self.assertRaisesRegex(RuntimeError, "failed"):
                router.main()
        self.assertEqual(router.load_state()["telegram_offset"], 12)


if __name__ == "__main__":
    unittest.main()
