# Discord → Telegram topics: automatic photo and video router

This is a **free**, scheduled GitHub Actions bot. It checks the text/announcement channels in **one Discord server** about every **10 minutes** and copies their image and video **attachments** into similarly named **topics inside one Telegram supergroup**.

Examples:

| Discord source channel | Telegram target topic | How it matches |
|---|---|---|
| `#wtj` | `WTJ` | Exact, case-insensitive |
| `#wtj-discord` | `WTJ` | Ignores “discord” suffix |
| `#sports-videos` | `Sports` | Ignores “videos” suffix |
| `#news-updates` | `News` | Ignores “updates” suffix |

The bot prints a full channel → topic match report in **Actions → latest run → Register Telegram topics and forward Discord media**. Only **unique, high-confidence** matches forward. Unknown or ambiguous names are skipped, never guessed.

**Important:** Telegram's public Bot API does **not provide a method to list all existing forum topics and their names**. Register each **existing** Telegram topic **once** by sending `/bind Exact Topic Name` **inside that topic**. After that, the router automatically discovers Discord channel names and chooses matching topics. New topic-creation events from the configured owner can also be recorded, but do not rely on that for existing topics.

## Step 1. Create and invite your Discord bot

1. Visit https://discord.com/developers/applications → **New Application**, name it `Discord Telegram Media Router`.
2. In **Bot**, enable **Message Content Intent** and **Save Changes**. This is needed to read attachment metadata in server messages. If the bot is verified, Discord may require additional intent approval.
3. On **Bot**, click **Reset Token** / **Copy** as appropriate, and save the token privately. This is the `DISCORD_BOT_TOKEN` secret. **Never upload or share the token.**
4. In **OAuth2 → URL Generator**, tick `bot`, then under **Bot Permissions** select **View Channels** and **Read Message History** (no Administrator permission needed).
5. Open the generated invite URL and add the bot to your Discord server. Ensure these permissions are not denied in the channels you want to monitor.
6. In Discord desktop: **User Settings → Advanced → Developer Mode** ON. Right-click **your server icon** → **Copy Server ID**. This is `DISCORD_GUILD_ID` (the **server ID**, **not** a channel ID).

This monitors every accessible standard **text** and **announcement** channel in that server. It does not yet monitor forum/media threads, DMs, or image/video embeds that are only external links.

## Step 2. Create and invite your Telegram bot

1. In Telegram, open https://t.me/BotFather → send `/newbot` and follow the prompts.
2. Copy the bot's token; it becomes `TELEGRAM_BOT_TOKEN` (keep it secret).
3. Open the bot's private chat and send `/start` followed by `hello`.
4. Make sure the destination Telegram chat is a **group with Topics enabled**. Telegram → group info → Edit/Manage Group → **Topics**, then enable topics and create/keep topic names such as `WTJ`, `Sports`, `News`.
5. **Add your Telegram bot to the group** and make it an admin **allowed to post messages and media**. It does not need permission to delete content or add users.
6. Open the `WTJ` topic as your normal Telegram user and send `/bind WTJ`. Do the same in each other existing topic, e.g. `/bind Sports` in the `Sports` topic and `/bind News` in `News`.
7. Open a private browser window or your regular browser and navigate to:

   `https://api.telegram.org/botYOUR_TELEGRAM_TOKEN/getUpdates`

   Replace `YOUR_TELEGRAM_TOKEN` with your real token **without** the word `bot` in front of it. This URL is sensitive; do not share it or take screenshots showing it.
8. In the JSON response, locate one of **your group commands** (`/bind WTJ`). Use:
   - `message.chat.id` (**negative**, typically starting `-100`) = `TELEGRAM_GROUP_ID`
   - `message.from.id` (your own **positive** Telegram user ID) = `TELEGRAM_OWNER_USER_ID`

   Check the `chat.title` field to ensure you selected **your Telegram group**. If you see only the private chat, return to the Telegram group and send a fresh `/bind WTJ` in the topic, then refresh. If JSON `result` is empty, verify you added the bot and sent the command. A new bot cannot receive old messages from before it joined.

**Registration security:** only commands sent by `TELEGRAM_OWNER_USER_ID` in `TELEGRAM_GROUP_ID` are accepted. Other members cannot change the routing with `/bind`.

## Step 3. Create a SEPARATE GitHub repository

1. Go to https://github.com/new and create `DiscordTelegramMediaRouter` (choose **Public** if you want public-repository GitHub Actions runners without the private-repo minute quota). Do not reuse your unrelated `WTJplaylistsync` repository.
2. Download/unzip the complete project package. In your new repository: **Add file → Upload files**, and upload these **root-level** files:
   - `media_router.py`
   - `requirements.txt`
   - `router_state.json`
   - `README.md` (optional)
   - `tests/test_matching.py` (optional, if uploading a folder is possible)
3. Commit the files.
4. In GitHub, select **Add file → Create new file**, enter the **exact filename** `.github/workflows/discord-to-telegram.yml`, and paste the contents of the downloaded workflow file. Click **Commit changes**. Make sure this filename starts with a dot and uses `/` as the directory separators.

The public repository will contain source code, Discord channel IDs, Telegram topic IDs and topic **names** in `router_state.json`; it will **not** contain your bot tokens or any image/video contents. If names and IDs must be private, use a private repo instead and monitor your GitHub Actions usage (a run every 10 minutes can exceed the free private-repo minutes).

## Step 4. Add five GitHub Actions secrets

GitHub repository → **Settings → Secrets and variables → Actions → New repository secret**. Add each one:

| Secret | Where it comes from |
|---|---|
| `DISCORD_BOT_TOKEN` | Discord Developer Portal → your app → Bot token |
| `DISCORD_GUILD_ID` | **Discord server** ID (not channel ID) |
| `TELEGRAM_BOT_TOKEN` | Telegram @BotFather bot token |
| `TELEGRAM_GROUP_ID` | `message.chat.id` of a `/bind` message in your Telegram group (usually `-100...`) |
| `TELEGRAM_OWNER_USER_ID` | `message.from.id` of **your** `/bind` message in that group |

Use the names exactly as printed, no surrounding quotes. Do not paste any tokens into `README.md`, Python files, the workflow YAML, Telegram messages, or an issue.

## Step 5. Start the workflow

1. GitHub → repository **Actions** tab → **Discord photos and videos to Telegram topics**.
2. Click **Run workflow** → **Run workflow**.
3. Open the finished run and examine **Register Telegram topics and forward Discord media**. It should show `Known Telegram topics` and a route report such as `#wtj-discord -> WTJ`. If the step fails, review its red error text.
4. **The first successful run intentionally ignores historical Discord messages** and starts its counters from the latest messages. This avoids flooding Telegram with old photos or videos.
5. Post **one NEW image** and **one NEW video** in Discord `#wtj` or `#wtj-discord`, then run the workflow manually again.
6. Look inside **Telegram group → WTJ topic**. Both should arrive. Further changes will be checked around every 10 minutes automatically (actual run timing can vary).

## Commands in Telegram topics

- `/bind WTJ` — in WTJ topic, register or update this existing topic's name (send from the configured Telegram owner account).
- `/bind Name With Spaces` — in any other topic, use that topic's exact current display name.
- `/unbind` — inside a topic, remove it from automatic matching.
- `/topics` — report the number of registered topics.

If you rename a Telegram topic later, use `/bind New Name` inside it again. Do not name two Telegram topics so similarly that either could be the destination; ambiguous matches are skipped. All commands are processed on the next scheduled GitHub run, not immediately.

## Limitations / troubleshooting

- **Sizes:** Telegram's hosted public Bot API accepts uploaded photos up to **10 MB** as photos and documents/videos up to **50 MB**. For images that cannot be uploaded as a photo, the bot attempts a document upload. For oversized **known-size** files, it posts a Discord message link rather than the actual media. The viewer still needs access to the source Discord channel. A video in MOV/MKV format is sent as a document, preserving the original file.
- **New uploads only:** It intentionally ignores older posts during the first run. If no topic was registered yet, unmatched media posted before binding will be skipped.
- **No duplicate guarantee after interrupted runs:** The bot saves progress to `router_state.json` and commits it back to GitHub after each run. Unexpected runner interruption or push failure can occasionally duplicate content on a retry; otherwise already processed attachments are skipped.
- **GitHub scheduling:** Scheduled executions may be delayed or dropped. GitHub can disable cron workflows in public repos after 60 days without repo activity; re-enable in Actions if needed.
- **No topics found:** Check that your bot was invited to the **right** Telegram group; the owner user ID matches you; and `/bind WTJ` was sent **inside** the WTJ topic. Give the workflow one run to process the commands. The step log prints the registered topic count.
- **Discord permission error / no attachments:** Confirm the bot is in the right server, can view and read channel history, and has Message Content Intent enabled. The channel report is displayed in the workflow log.
- **403 pushing state to GitHub:** Go to Settings → Actions → General → Workflow permissions. Ensure Actions are permitted to use read and write permissions for repository contents. The workflow also declares `permissions: contents: write`.
- **Telegram 409 conflict:** A separate program/webhook is consuming the same bot's Telegram updates. Use this bot only for the router; a bot can receive updates via either `getUpdates` **or** webhook, not both.
- **Security:** Only copy media from Discord channels where you have permission to republish it to the Telegram group, especially if either server/group is private.

### Technical information

- Python 3.13, `requests`, Discord REST API v10, Telegram Bot API, GitHub Actions.
- No self-bot or personal Discord user token. No Discord Nitro or Telegram Premium.
- State file contains the Telegram topic registrations, per-Discord-channel cursors, and the Telegram update offset. It is deliberately checked into Git and updated by the action after each run.
- The workflow never stores the photos/videos in your repository.
- To test locally: `python -m pip install -r requirements.txt` then `python -m unittest discover -s tests -v`.
