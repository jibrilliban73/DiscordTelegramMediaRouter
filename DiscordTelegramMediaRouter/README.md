# Discord → Telegram router setup

The current workflow and configuration schema are documented in the [main README](../README.md). The workflow already uses this repository's nested Python folder.

## Discord bot

1. Open the Discord Developer Portal and your existing bot application.
2. Enable **Message Content Intent**. The bot needs message content to see attachment metadata and direct media URLs.
3. Invite it to your source server with **View Channel** and **Read Message History**. Check individual channel permission overrides too.
4. Save its token only in the `DISCORD_BOT_TOKEN` repository secret. The source server ID is already configured in the workflow.

Standard text and announcement channels are supported. Forum/media threads and DMs are not monitored by this version.

## Telegram bot and topics

1. Use your existing Telegram bot, or create one with the official BotFather.
2. Add it to the destination supergroup and allow it to post photos/videos in forum topics.
3. Save the bot token as `TELEGRAM_BOT_TOKEN`, the group ID as `TELEGRAM_GROUP_ID`, and your Telegram user ID as `TELEGRAM_OWNER_USER_ID`.
4. Store the existing topic IDs/names and explicit channel-to-topic overrides in the `ROUTER_CONFIG_JSON` secret. Do not upload your real private mapping to this public repository.

For a topic not in the private configuration, send `/bind Exact Topic Name` inside that topic from the configured owner account. Commands are processed by the next sync. Other members cannot register or change routes.

- `/bind Topic Name` registers or renames an existing topic.
- `/unbind` removes a registered topic. Seeded topics must also be removed from `ROUTER_CONFIG_JSON`.
- `/topics` reports the registered topic count.

The bot cannot list all old Telegram forum topics by itself. Topic IDs must come from the configured inventory or a command inside each topic.

## Start and verify

1. Open **Actions → Discord photos and videos to Telegram topics → Run workflow**.
2. Leave mode as `sync` and run it. Mode `tests` runs the automated tests without connecting the bots.
3. Confirm that the run finds registered topics and matched channels. Private channel/topic names are omitted from public logs.
4. The first successful run initializes each matched channel at its newest message, skipping history.
5. Post a new photo or video in a matched Discord channel. A direct Discord-hosted image/video attachment URL also qualifies. Check that the actual file arrives in the matching Telegram topic after the next sync.

The schedule runs about every 10 minutes and can be delayed by GitHub. Your computer does not need to stay awake.

## Files and reliability

- Only photos/videos are forwarded. Ordinary links, website previews, and other documents are ignored.
- Discord-hosted attachment URLs are downloaded and uploaded as media, with their signatures preserved for downloading. Duplicate references to the same file within a message are merged.
- MP4 videos use Telegram video uploads; unsupported media previews fall back to uploading the original photo/video file as a document.
- Media over the configured hosted Bot API upload limit is skipped and flagged. No replacement text or link is posted, and later media can still proceed.
- Progress is saved after each attachment, encrypted in `router_state.enc`, and committed even after a partial failure. Interrupted uploads or failed state pushes can still cause a duplicate on retry.
- The state encryption key is derived from the Telegram bot token. If you rotate that token, migrate the state before resuming. The router refuses to overwrite state it cannot decrypt.
- The old blank `router_state.json` is a legacy stub; the current workflow uses only the encrypted state file.

## Troubleshooting

- **Missing secrets:** add the exact names listed in the main README.
- **No topics:** supply the private configuration or send the owner-only topic binding commands.
- **No channel match:** check names or add an explicit channel-ID override. Duplicate or ambiguous topic names are skipped.
- **Discord 403 / missing media:** check bot membership, channel permissions, and Message Content Intent.
- **Expired direct media URL:** a copied Discord CDN URL can expire. Use a fresh Discord media link or upload the file again.
- **Telegram 409:** another program or webhook is consuming updates for this bot. Use one update consumer per bot.
- **State push failure:** the workflow needs repository contents write permission. It declares that permission for the sync job only.

Run tests from this folder with `python -m unittest discover -s tests -v` after installing `requirements.txt`.
