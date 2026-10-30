# Discord → Telegram photos and videos

Copies new uploaded photos/videos and direct Discord-hosted photo/video URLs from matching Discord channels into the corresponding Telegram topics. Telegram receives actual media files.

Configured Discord server: `987533334894772284` (from your Discord link).

The workflow runs about every 10 minutes. GitHub may delay scheduled runs; this is not instant forwarding and does not require a computer to stay awake.

## Finish connecting your bots

Add these repository secrets under **Settings → Secrets and variables → Actions**:

| Secret | Value |
|---|---|
| `DISCORD_BOT_TOKEN` | Token for your existing Discord bot, application `1564756749079150684` |
| `TELEGRAM_BOT_TOKEN` | Token for your Telegram bot from BotFather |
| `TELEGRAM_GROUP_ID` | Destination supergroup ID, usually starting with `-100` |
| `TELEGRAM_OWNER_USER_ID` | Your Telegram user ID, accepted for topic registration |
| `ROUTER_CONFIG_JSON` | Private topic names/IDs and explicit channel-ID overrides; schema below |

The Discord server ID is already configured in the workflow. Tokens belong only in encrypted secrets.

1. Ensure your Discord bot is in this server with **View Channel**, **Read Message History**, and **Message Content Intent** enabled.
2. Add the Telegram bot to your destination group and allow it to post photos/videos in topics.
3. Supply the existing Telegram topic IDs in `ROUTER_CONFIG_JSON`. For future topics you can also send `/bind Exact Topic Name` from your own account inside the topic. The channel `wtj` or `wtj-discord` will match a topic named `WTJ`.
4. Open **Actions → Discord photos and videos to Telegram topics → Run workflow**, leave mode as `sync`, and run it once.
5. After the first successful run, upload a **new** image and video to a matching Discord channel. The next sync forwards them to its Telegram topic.

Existing topics need their actual topic IDs, supplied privately or registered once. Ambiguous names are skipped. The first successful channel initialization skips older posts to avoid copying the entire history.

## Private routing configuration

Keep the real configuration in the encrypted `ROUTER_CONFIG_JSON` secret. This generic example maps channel `123` to topic `20`, regardless of the Discord channel's name:

```json
{
  "discord_guild_id": "456",
  "telegram_group_id": "-100789",
  "topics": {"20": {"name": "Archive"}},
  "channel_overrides": {"123": "20"}
}
```

Overrides win over name matching and remain valid after a Discord channel rename. If an explicit destination is missing, that channel is skipped until fixed. Other channels use unique exact or sufficiently similar names. Edit the private configuration to update or remove its seeded topics.

## Behavior and checks

- Photos and videos are copied as files. Direct Discord-hosted attachment URLs also qualify, including signed URLs. Other websites, Discord message links, documents, and ordinary website previews are ignored.
- An attachment and a link/embed pointing to that same Discord media file produce one copy.
- New media in matched channels is processed oldest first. Existing progress is saved after each attachment and committed even if a later attachment fails.
- Unmatched channels keep any existing progress while you repair their names or bindings.
- Missing credentials, no topic registrations, and no accessible matched channels produce a failed sync with an actionable message.
- Oversized files are skipped and flagged in the workflow, while other files continue. They are never replaced with text or links. The run reports a failure when files were skipped for size.
- Private topic registrations and forwarding cursors are saved in `router_state.enc`, encrypted using a key derived from the Telegram bot token. Public logs omit channel/topic names and signed CDN URLs. Keep the same bot token when resuming existing state; a token change requires a deliberate state migration.
- Normal completed runs skip already forwarded attachments. An interrupted upload or failed state push can still cause a duplicate on retry.
- Code pushes run tests. Scheduled and manual `sync` runs test first, then forward media. Manual `tests` mode never connects the bots.

See [the detailed setup guide](DiscordTelegramMediaRouter/README.md) for bot setup, topic commands, and troubleshooting.

Run tests locally:

```sh
cd DiscordTelegramMediaRouter
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```
