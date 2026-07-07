#!/usr/bin/env python3
"""
Build the "Sacramento's Pros" page.

Pipeline:
  1. Read roster.json (your curated list of hometown athletes).
  2. Resolve each athlete to their ESPN athlete id (via a per-league active-player
     index, or an espnId you pin in roster.json).
  3. Pull their CURRENT team + a headline stat line from ESPN's athlete endpoint.
  4. Render a clean, brand-styled public/index.html for GitHub Pages.

Everything network-related is best-effort and defensive: if a lookup fails, the
athlete still appears using the fallback info in roster.json. The page ALWAYS
renders. Nothing is invented — a missing stat is simply left off.

ESPN endpoints (confirmed shapes):
  - Active index: https://sports.core.api.espn.com/v3/sports/{sport}/{league}/athletes?limit=20000&active=true
       -> {"items":[{"id","fullName","displayName","active"}, ...]}
  - Athlete:      https://site.web.api.espn.com/apis/common/v3/sports/{sport}/{league}/athletes/{id}
       -> {"athlete":{"team":{"displayName"}, "statsSummary":{"statistics":[
              {"abbreviation","shortDisplayName","displayName","displayValue"}, ...]}}}
"""

import os
import re
import json
import html
import unicodedata
import datetime

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SPORT_PATHS = {
    "mlb": ("baseball", "mlb"),
    "nfl": ("football", "nfl"),
    "nba": ("basketball", "nba"),
}

SECTIONS = [("mlb", "Baseball"), ("nfl", "Football"), ("nba", "Basketball")]

NAVY = "#1F3A5F"
BROWN = "#8A5A1F"

TIMEOUT = 25
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SacProsBot/1.0)"}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# Cache of {league_code: {normalized_name: id}} so we fetch each index once.
_INDEX_CACHE = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_json(url):
    try:
        r = SESSION.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            print(f"  [warn] {r.status_code} for {url[:80]}")
            return None
        return r.json()
    except Exception as e:
        print(f"  [warn] fetch failed ({e}) for {url[:80]}")
        return None


def norm(name):
    """Normalize a name for matching: lowercase, strip accents & punctuation."""
    if not name:
        return ""
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = n.lower()
    n = re.sub(r"[.\-'’]", "", n)      # drop periods, hyphens, apostrophes
    n = re.sub(r"\s+", " ", n).strip()
    return n


def league_index(league):
    """Return {normalized_name: id} for a league's active athletes (cached)."""
    if league in _INDEX_CACHE:
        return _INDEX_CACHE[league]

    sport, lg = SPORT_PATHS[league]
    url = (f"https://sports.core.api.espn.com/v3/sports/{sport}/{lg}"
           f"/athletes?limit=20000&active=true")
    data = _get_json(url)
    full_map, initial_map = {}, {}
    if data and isinstance(data.get("items"), list):
        for it in data["items"]:
            _id = it.get("id")
            full = it.get("fullName") or it.get("displayName") or ""
            if not _id or not full.strip():
                continue
            active = bool(it.get("active"))
            key = norm(full)
            # Prefer active players when the same normalized name repeats.
            if key and (key not in full_map or active):
                full_map[key] = _id
            # Secondary key: first initial + last name (helps Cam/Cameron etc.)
            parts = key.split(" ")
            if len(parts) >= 2:
                ik = parts[0][0] + " " + parts[-1]
                if ik not in initial_map or active:
                    initial_map[ik] = _id
        print(f"  [index] {league}: {len(full_map)} names loaded")
    else:
        print(f"  [index] {league}: index unavailable")

    _INDEX_CACHE[league] = (full_map, initial_map)
    return _INDEX_CACHE[league]


def resolve_id(name, league, espn_id):
    """espnId override -> exact name -> first-initial+last-name fallback."""
    if espn_id and str(espn_id).strip():
        return str(espn_id).strip()
    full_map, initial_map = league_index(league)
    key = norm(name)
    if key in full_map:
        return full_map[key]
    parts = key.split(" ")
    if len(parts) >= 2:
        ik = parts[0][0] + " " + parts[-1]
        if ik in initial_map:
            return initial_map[ik]
    return None


def fetch_athlete(league, athlete_id):
    """Return {'team': str|None, 'stats': [(label, value), ...]} for one id."""
    sport, lg = SPORT_PATHS[league]
    url = (f"https://site.web.api.espn.com/apis/common/v3/sports/{sport}/{lg}"
           f"/athletes/{athlete_id}")
    out = {"team": None, "stats": []}
    data = _get_json(url)
    if not data:
        return out

    ath = data.get("athlete") if isinstance(data.get("athlete"), dict) else data

    team = ath.get("team")
    if isinstance(team, dict):
        out["team"] = team.get("displayName") or team.get("name")

    summary = ath.get("statsSummary")
    if isinstance(summary, dict) and isinstance(summary.get("statistics"), list):
        for s in summary["statistics"][:4]:
            if not isinstance(s, dict):
                continue
            label = (s.get("abbreviation") or s.get("shortDisplayName")
                     or s.get("displayName") or s.get("name"))
            value = s.get("displayValue")
            if value in (None, "") and s.get("value") is not None:
                value = s["value"]
            if label and value not in (None, ""):
                out["stats"].append((str(label), str(value)))
    return out


def enrich(a):
    league = a.get("league")
    a.setdefault("stats", [])
    if league not in SPORT_PATHS:
        return a
    aid = resolve_id(a.get("name", ""), league, a.get("espnId"))
    if not aid:
        print(f"  [miss] {a.get('name')} — no id resolved")
        return a
    live = fetch_athlete(league, aid)
    if live.get("team"):
        a["team"] = live["team"]
    a["stats"] = live.get("stats", [])
    print(f"  [ok]   {a.get('name')} -> id {aid}, {len(a['stats'])} stats")
    return a


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def esc(s):
    return html.escape(str(s if s is not None else ""))


def card_html(a):
    stats = a.get("stats") or []
    chips = "".join(
        f'<span class="stat"><b>{esc(v)}</b> {esc(label)}</span>'
        for (label, v) in stats
    ) or '<span class="stat muted">stats update on game days</span>'
    team = esc(a.get("team")) or "—"
    return f"""
      <article class="card">
        <div class="card-top">
          <h3>{esc(a['name'])}</h3>
          <span class="pos">{esc(a.get('position'))}</span>
        </div>
        <div class="team">{team}</div>
        <div class="stats">{chips}</div>
        <div class="home">Sacramento roots: {esc(a.get('hometown'))}</div>
      </article>"""


def render(athletes, updated):
    body = ""
    for code, title in SECTIONS:
        group = [a for a in athletes if a.get("league") == code]
        if not group:
            continue
        cards = "".join(card_html(a) for a in group)
        body += f'\n      <section>\n        <h2>{esc(title)}</h2>\n        <div class="grid">{cards}</div>\n      </section>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sacramento's Pros</title>
<style>
  :root {{ --navy:{NAVY}; --brown:{BROWN}; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:24px; color:#222; background:#fff; line-height:1.45;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Lato,"Helvetica Neue",Arial,sans-serif; }}
  header.page {{ border-bottom:3px solid var(--brown); padding-bottom:14px; margin-bottom:22px; }}
  header.page h1 {{ margin:0; color:var(--navy); font-size:1.7rem; }}
  header.page p {{ margin:6px 0 0; color:#555; font-size:.95rem; }}
  h2 {{ color:var(--navy); border-left:5px solid var(--brown); padding-left:10px; margin:26px 0 12px; font-size:1.25rem; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:14px; }}
  .card {{ border:1px solid #e4e4e4; border-radius:10px; padding:14px 16px; background:#fafafa; }}
  .card-top {{ display:flex; align-items:baseline; justify-content:space-between; gap:8px; }}
  .card h3 {{ margin:0; color:var(--navy); font-size:1.05rem; }}
  .pos {{ font-size:.72rem; font-weight:700; color:#fff; background:var(--brown); padding:2px 7px; border-radius:20px; white-space:nowrap; }}
  .team {{ margin:4px 0 8px; font-weight:600; color:#333; }}
  .stats {{ display:flex; flex-wrap:wrap; gap:6px; margin-bottom:8px; min-height:24px; }}
  .stat {{ font-size:.8rem; background:#fff; border:1px solid #e0e0e0; border-radius:6px; padding:2px 7px; }}
  .stat b {{ color:var(--navy); }}
  .stat.muted {{ color:#999; font-style:italic; border-style:dashed; }}
  .home {{ font-size:.78rem; color:#777; border-top:1px dashed #ddd; padding-top:7px; }}
  footer.page {{ margin-top:28px; padding-top:12px; border-top:1px solid #eee; color:#888; font-size:.8rem; }}
</style>
</head>
<body>
  <header class="page">
    <h1>Sacramento's Pros</h1>
    <p>Active professional athletes with Sacramento-area roots — where they play now, and how they're doing.</p>
  </header>{body}
  <footer class="page">
    Updated {esc(updated)}. Team &amp; stats via public sports data; roster curated by Michael Rehm.
    Off-season stat lines reflect the most recent completed season.
  </footer>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    with open("roster.json", "r", encoding="utf-8") as f:
        roster = json.load(f)
    athletes = roster.get("athletes", [])

    print(f"Enriching {len(athletes)} athletes...")
    for a in athletes:
        try:
            enrich(a)
        except Exception as e:
            print(f"  [error] {a.get('name')}: {e}")
            a.setdefault("stats", [])

    updated = datetime.datetime.now(datetime.timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
    os.makedirs("public", exist_ok=True)
    with open("public/index.html", "w", encoding="utf-8") as f:
        f.write(render(athletes, updated))
    print(f"Built public/index.html with {len(athletes)} athletes.")


if __name__ == "__main__":
    main()
