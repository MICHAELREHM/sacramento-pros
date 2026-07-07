# Sacramento's Pros — a self-updating page of hometown athletes

This is a small, self-running website that lists active pro athletes with
Sacramento-area roots — their current team and a headline stat line — and
**rebuilds itself once a day on its own**, for free, on GitHub. You then show it
on your real estate site with a one-line embed.

You do **not** run anything on your own computer. GitHub runs it for you.

---

## What's in here

| File | What it is |
|------|------------|
| `roster.json` | **The only file you'll normally touch.** Your list of athletes. |
| `build.py` | The script that fetches current stats and builds the page. |
| `requirements.txt` | Tells GitHub which Python library to install. |
| `.github/workflows/build.yml` | The daily "run this and publish it" instruction. |

---

## One-time setup (about 10 minutes, all in a web browser)

### 1. Make a free GitHub account
Go to <https://github.com> and sign up. Free is all you need.

### 2. Create a new repository
- Click the **+** (top-right) → **New repository**.
- **Name:** `sacramento-pros` (or anything you like).
- Set it to **Public**. (Free GitHub Pages requires public — this page has no secrets in it, so public is fine.)
- Click **Create repository**.

### 3. Upload these files
- On the new repo page, click **uploading an existing file** (or **Add file → Upload files**).
- Drag in **all** of these, keeping the folder structure:
  - `roster.json`
  - `build.py`
  - `requirements.txt`
  - the `.github` folder (with `workflows/build.yml` inside it)
- Scroll down, click **Commit changes**.

> Tip: the easiest way to preserve the `.github/workflows/` folder is to unzip the
> package on your computer first, then drag the **whole unzipped folder's contents**
> into the upload box. GitHub keeps the folders.

### 4. Turn on GitHub Pages
- In the repo, go to **Settings** → **Pages** (left sidebar).
- Under **Build and deployment → Source**, choose **GitHub Actions**.
- That's it — no branch to pick.

### 5. Run it once
- Go to the **Actions** tab.
- If it asks you to enable workflows, click to enable.
- Click **"Build & publish Sacramento's Pros"** → **Run workflow** → **Run workflow**.
- Wait ~1–2 minutes. A green check means it built and published.

### 6. Find your page's address
- Back in **Settings → Pages**, the URL appears at the top, like:
  `https://YOURNAME.github.io/sacramento-pros/`
- Open it. You should see your athletes, grouped by sport.

---

## Show it on your real estate site (WordPress)

On the page where you want it (e.g. a "Sacramento's Pros" page):

1. Add a **Custom HTML** block.
2. Paste this, swapping in your real Pages URL:

```html
<iframe
  src="https://YOURNAME.github.io/sacramento-pros/"
  style="width:100%; height:1400px; border:0;"
  loading="lazy"
  title="Sacramento's Pro Athletes"></iframe>
```

3. Publish. It appears inside your site, styled to match (navy + brown).
   Adjust the `height` number if it's too tall or too short.

(If you'd rather not embed, just **link** to the GitHub Pages URL — also fine.)

---

## Keeping the roster current

Open `roster.json` on GitHub (click the file → the pencil icon to edit).

- **Add someone:** copy a line, change the details. `league` must be `mlb`, `nfl`, or `nba`.
- **Remove someone:** delete their line.
- **`team`** is a fallback — leave it blank and the live data fills in their current team when available.
- **`espnId`** is optional. Leave it blank and the script finds the player by name.
  If a common name pulls up the wrong person, open that player's page on
  espn.com and copy the number from their URL into `espnId` to lock it.

Click **Commit changes** and the page rebuilds automatically within a minute.

---

## Good to know

- **Stats populate as games are played.** In the off-season a league's stat line
  may be sparse; that's expected. The roster, positions, teams, and hometowns
  always show.
- **The stat fetch is best-effort.** It pulls from public sports data, and if a
  particular player doesn't resolve on the first run, they still appear — we can
  fine-tune that player by adding their `espnId`.
- **Scheduled runs pause after 60 days of no activity** on a free repo. The daily
  publish keeps it awake; if you ever leave it untouched for two months, just open
  the Actions tab and click **Run workflow** once to wake it back up.
- **Change the update time** by editing the `cron:` line in
  `.github/workflows/build.yml` (it's in UTC).
