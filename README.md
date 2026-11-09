# ClipRush

Finds the most controversial, shocking and curious moments in new celebrity, athlete, NBA and adult-star podcasts minutes after they upload. It writes a caption for each moment and has a batch ready for your 7 AM and 9 PM (IST) posts on X. It also studies real clip accounts on X to learn what gets views.

**Cost: ₹0.** It runs on GitHub's free plan, uses free public data, and free AI tiers are optional. Nothing logs in to X.

```
 GitHub cloud (free, every 5 min, 24/7)        Your phone + laptop
 ┌────────────────────────────────────┐        ┌──────────────────────────────┐
 │ watch 42 podcasts + 80 people       │        │ ClipRush app (same on both)  │
 │ read captions + viewer comments     │  ───►  │  • batches for 7 AM / 9 PM   │
 │ score moments, write captions       │ encrypted│ • preview the exact moment  │
 │ study X clip accounts (no login)    │  data  │  • copy caption, approve     │
 └────────────────────────────────────┘        └──────────────────────────────┘
                                                Laptop (when it's on): finishes anything the
                                                cloud couldn't read, then auto-downloads and
                                                cuts every top clip into C:\Users\Sandeep\ClipRush\<date>
```

---

## Fastest setup (laptop): `SETUP_EASY.bat`
1. Save your notes file (password, Groq, Gemini, Apify and GitHub keys) as a `.txt` with "clip" and "password" in its name, e.g. `Cliprushpassword.txt`. Put it next to this folder.
2. Double-click **SETUP_EASY.bat**. It:
   - copies ClipRush to `C:\ClipRush` and installs Python, FFmpeg and the packages;
   - reads your keys from the notes file and checks each one;
   - opens the app already unlocked, plus a QR code for your iPhone;
   - schedules the background runs, adds **ClipRush - Find clips now** to your Desktop, and makes your first clips.

Never upload the notes file to GitHub. `.gitignore` blocks it in GitHub Desktop, but the web uploader would not.

## Setup (about 30 minutes, one time)

### 1. Put ClipRush on GitHub (free)
1. Create a free account at github.com.
2. Click **+ → New repository**. Name it `cliprush`, choose **Public** (that keeps it free; your data is encrypted), then create it.
3. Click **uploading an existing file**. Drag **everything inside this ZIP's `cliprush` folder** into the page, then **Commit changes**.
   Check that the folder `.github/workflows` was uploaded. If it's missing, create the two files by hand: **Add file → Create new file**, name it `.github/workflows/brain.yml`, and paste the contents in. Do the same for `keepalive.yml`.

### 2. Add your password and free keys (Settings → Secrets and variables → Actions → New repository secret)
| Secret | Required? | Where to get it |
|---|---|---|
| `CLIPRUSH_PASSWORD` | **Yes** | Make one up (8+ characters). The app asks for it. |
| `GROQ_API_KEY` | Recommended | console.groq.com → API Keys → Create (free, no card) |
| `GEMINI_API_KEY` | Recommended (backup AI) | aistudio.google.com → Get API key (free, no card) |
| `APIFY_TOKEN` | Optional | apify.com free plan → Settings → Integrations. Capped at $4.50 of the free $5/month. |
| `YT_COOKIES` | Optional | Only if the free mirrors also fail. Use a **throwaway** Google account, never your main one. Log in to YouTube in an InPrivate window and export cookies.txt with a "Get cookies.txt" browser extension. Paste the file's text here. |

Without any AI key, moments are still picked by the rule engine and captions come from templates. With a key, an AI also judges each moment and writes better captions.

### 3. Turn on the cloud brain
**Actions** tab → enable workflows → **ClipRush brain** → **Run workflow**. The first run takes a few minutes because it looks up every podcast channel. After that it runs by itself every 5 minutes.

### 4. Turn on the app
**Settings → Pages** → Source: *Deploy from a branch* → Branch: `main`, folder `/docs` → Save.
Your app is at `https://<your-github-name>.github.io/cliprush/`.

### 5. Install on iPhone
Open that link in **Safari** → Share → **Add to Home Screen**. Open it and enter your password. On the laptop, open the same link in Edge.

### 6. Laptop auto-downloader
1. Unzip ClipRush to `C:\ClipRush`.
2. Create a GitHub token: Settings → Developer settings → **Fine-grained tokens** → Generate. Repository access: only `cliprush`. Permissions: **Contents: Read and write**.
3. Double-click `laptop\install_windows.bat`. It installs Python and FFmpeg (both free), opens `cliprush.env` for you to fill in (repo, token, password), registers a silent background task (every 30 minutes and at sign-in), and runs the self-test.

Whenever the laptop is on, it:
- finishes episodes the cloud couldn't read;
- downloads **only the seconds of each clip** (not the whole episode);
- makes 16:9 1080p MP4s with burned-in captions and a 3-second hook;
- saves them with a caption `.txt` into `C:\Users\Sandeep\ClipRush\<date>\`. Clips you approved are cut first.

### 7. "Find clips now" button
- **App:** tap **⚡ Find clips**. Paste a YouTube link to clip that exact video (any age, any channel), or leave it empty to check every podcast now. Progress shows at the top, and clips usually appear within 2–4 minutes.
  The first time, the app asks for a GitHub fine-grained token with **Contents: Read and write** and **Actions: Read and write** on the `cliprush` repo.
- **Laptop:** double-click **ClipRush - Find clips now** on your Desktop. Paste a link or press Enter. The laptop reads YouTube directly (never blocked), cuts the MP4s and opens the folder.

### When YouTube blocks GitHub's cloud
YouTube sometimes asks GitHub's servers to "confirm you're not a bot". ClipRush then automatically reads captions and comments through free public mirrors (Invidious and Piped). The Health tab shows which mirror it used. If the mirrors fail too, the episodes wait for your laptop, or you can add the optional `YT_COOKIES` secret.

### 8. Optional extras
- **Exact timing.** GitHub's 5-minute schedule can run 5–30 minutes late at busy times. A free job at cron-job.org can trigger it on time instead:
  - URL: `https://api.github.com/repos/<you>/cliprush/dispatches`, method POST, every 3 minutes
  - Headers: `Authorization: Bearer <fine-grained token with Contents: write>` and `Accept: application/vnd.github+json`
  - Body: `{"event_type":"tick"}`
- **Phone cutting (beta).** Install **a-Shell** (free) and run `pip install yt-dlp` in it. Copy `phone/cliprush_phone.py` into a-Shell's folder using the Files app. Then make a Shortcut called **ClipRush Cut** with these actions:
  1. a-Shell *Execute Commands*: `python3 cliprush_phone.py Shortcut Input` (pick the Shortcut Input variable), then on a new line `sh ~/Documents/ClipRush/cut.sh`
  2. *Get Contents of Folder*: a-Shell → ClipRush
  3. *Filter Files*: extension is mp4
  4. *Save to Photo Album*

  After that, **Approve** in the app cuts that one clip on your phone. Test it once. If iOS blocks it, use the next option.
- **Approved clips to Telegram.** Make a free bot with @BotFather, then put `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `cliprush.env`. The laptop sends each approved clip to your phone, where you save it to Photos.
- **Sync approvals between phone and laptop.** Happens automatically once the app has your token.

---

## Daily use
1. Open the app → **Batches**. The Morning (7:00 AM) and Night (9:00 PM) batches are each ready 30 minutes early: the top 5 clips, at most 2 from one episode.
2. Tap **Preview** to watch the exact moment and check who is speaking.
3. Edit the caption if you want (aim for 8–12 words, no emoji), then tap **Copy**.
4. Post the MP4 from your laptop folder (or approve it on your phone). Then use **More → Mark posted** and paste your post link; ClipRush learns from your own results too.

Avoid posting between 5:30 and 8:30 PM IST. Your own data showed that window is dead.

## How a moment is picked (all free)
- **Viewer comments with timestamps** ("47:12 he really said that"). This is the strongest early signal, and the app re-reads comments 45 minutes, 3 hours and 8 hours after upload.
- **What is said:** controversy, reveals, relationships, money, hot takes, shock, and famous names (tiered).
- **The episode headline and chapters.** Podcasts tease their hottest moment there.
- **Laughter, set-up questions, and YouTube's "most replayed" graph** when it exists.
- **Penalties** for sponsor reads, intros and outros, music, and fragments. Clips are trimmed to 8–22 seconds, starting on the set-up question.
- **What the X study learns.** People and topics that beat their account's normal views get boosted. Every number is shrunk until there's enough evidence, so one lucky post can't fool it.
- **Optional free AI** scores how viral and how self-contained each moment is, then writes the caption in the style of real 1M+ clips.

## Limits
- **YouTube sometimes blocks cloud servers** from reading captions and comments. When that happens, the app shows "waiting for laptop" and your laptop finishes those episodes automatically. Upload detection (RSS) usually keeps working from the cloud.
- **X data comes from public, no-login sources** (FxTwitter and X's embed timeline). They have worked for years, but they are unofficial and could change. Apify is the capped backup.
- **Free tiers (Groq, Gemini, Apify, GitHub) can change their rules.** Every one of them is optional, and the app keeps running on rules alone.
- **GitHub expects scheduled workflows to serve the project in the repository.** ClipRush is that project, but GitHub could still pause heavy schedules. If it ever does, the laptop keeps working.
- **Speaker names are a best guess** from the episode title. Always check who is talking in the preview before posting.
- **Copyright.** Posting other creators' clips carries the same DMCA-takedown risk on X that every clip account has.

## Check that everything works
```
python -m brain selftest --online
```
Every part reports PASS, WARN or FAIL, with the fix.

## Editing what it watches
Edit `config/sources.yaml` on GitHub (podcasts, people, X accounts) and `config/settings.yaml` (posting times, clip length, thresholds). The laptop downloads your edited config automatically. If a podcast handle is wrong, the Health tab lists it.

## Tests
```
pip install -r requirements.txt pytest && python -m pytest -q tests
```

## Updating ClipRush
In GitHub Desktop: copy the new files over your `cliprush` folder (replace all), type "update", then **Commit to main** and **Push origin**. On the laptop, also unzip the new version over `C:\ClipRush`.
