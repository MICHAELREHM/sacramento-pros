#!/usr/bin/env python3
"""
Build the "Sacramento's Pros" page.

What it does:
  1. Reads roster.json (your curated list of hometown athletes).
  2. For each athlete, tries to pull their CURRENT team + a headline stat line
     from ESPN's public JSON endpoints. This is best-effort: if anything fails,
     the athlete still appears using the fallback info in roster.json.
  3. Writes a clean, brand-styled public/index.html ready for GitHub Pages.

Design principle: the page must ALWAYS render. Network hiccups, an ESPN API
change, or a name that doesn't resolve should never blank the page — they just
mean that one athlete shows without a live stat line. Nothing here is invented;
if a stat can't be fetched, it's simply left off.
"""

import json
import html
import datetime
import urllib.parse

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Maps our simple league codes to ESPN's (sport, league) path segments.
SPORT_PATHS = {
    "mlb": ("baseball", "mlb"),
    "nfl": ("football", "nfl"),
    "nba": ("basketball", "nba"),
}

# Display order + friendly section headings.
SECTIONS = [
    ("mlb", "Baseball"),
    ("nfl", "Football"),
    ("nba", "Basketball"),
]

# Brand palette (matches the real estate site).
NAVY = "#1F3A5F"
BROWN = "#8A5A1F"

TIMEOUT = 12
HEADERS = {
    # A normal-looking UA; ESPN's public endpoints are friendlier with one.
    "User-Agent": "Mozilla/5.0 (compatible; SacProsBot/1.0; +https://github.com)"
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ---------------------------------------------------------------------------
# ESPN helpers  (all wrapped so they NEVER raise up to the caller)
# ---------------------------------------------------------------------------

def _get_json(url):
    """GET a URL and return parsed JSON, or None on any problem."""
    try:
        r = SESSION.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _walk(obj):
    """Yield every dict nested anywhere inside a JSON structure."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def resolve_espn_id(name, league):
    """
    Try to find an athlete's ESPN id by name, restricted to the right league.
    Returns a string id or None. Structure-agnostic: we walk the whole search
    response looking for a dict that clearly describes this athlete.
    """
    sport, lg = SPORT_PATHS[league]
    q = urllib.parse.quote(name)
    data = _get_json(f"https://site.web.api.espn.com/apis/search/v2?limit=12&query={q}")
    if not data:
        return None

    name_l = name.lower()
    for d in _walk(data):
        # An athlete result generally has an id, a display name, and some
        # indication of its sport/league. We match defensively.
        disp = str(d.get("displayName") or d.get("name") or "").lower()
        if not disp or disp != name_l:
            continue
        blob = json.dumps(d).lower()
        if lg in blob or sport in blob:
            _id = d.get("id") or d.get("uid") or d.get("guid")
            if _id:
                # uid/guid sometimes look like "s:20~l:28~a:12345"; grab trailing digits.
                s = str(_id)
                if s.isdigit():
                    return s
                if "a:" in s:
                    tail = s.split("a:")[-1]
                    tail = "".join(ch for ch in tail if ch.isdigit())
                    if tail:
                        return tail
    return None


def fetch_athlete(league, espn_id):
    """
    Pull current team + a short list of headline stats for one athlete.
    Returns {"team": str|None, "stats": [(label, value), ...]}.
    Uses ESPN's own 'stats summary' so we don't hard-code which stats matter
    per position — we just show what ESPN shows on the player's profile.
    """
    sport, lg = SPORT_PATHS[league]
    out = {"team": None, "stats": []}
    data = _get_json(
        f"https://site.web.api.espn.com/apis/common/v3/sports/{sport}/{lg}/athletes/{espn_id}"
    )
    if not data:
        return out

    # Current team: find a 'team' dict with a display name.
    for d in _walk(data):
        team = d.get("team")
        if isinstance(team, dict):
            tn = team.get("displayName") or team.get("name")
            if tn:
                out["team"] = tn
                break

    # Headline stats: ESPN uses a summary list of {label/displayName, displayValue}.
    for d in _walk(data):
        stats = d.get("statistics") or d.get("stats")
        if isinstance(stats, list) and stats:
            picked = []
            for s in stats:
                if not isinstance(s, dict):
                    continue
                label = s.get("label") or s.get("displayName") or s.get("name") or s.get("abbreviation")
                value = s.get("displayValue") or s.get("value")
                if label and value is not None:
                    picked.append((str(label), str(value)))
                if len(picked) >= 4:
                    break
            if picked:
                out["stats"] = picked
                break

    return out


def enrich(athlete):
    """Add live team/stats to a roster entry, non-destructively."""
    league = athlete.get("league")
    if league not in SPORT_PATHS:
        return athlete
    espn_id = (athlete.get("espnId") or "").strip() or resolve_espn_id(athlete["name"], league)
    if espn_id:
        live = fetch_athlete(league, espn_id)
        if live.get("team"):
            athlete["team"] = live["team"]
        athlete["stats"] = live.get("stats", [])
    else:
        athlete.setdefault("stats", [])
    return athlete


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def esc(s):
    return html.escape(str(s or ""))


def card_html(a):
    stats = a.get("stats") or []
    stat_chips = "".join(
        f'<span class="stat"><b>{esc(v)}</b> {esc(label)}</span>'
        for (label, v) in stats
    )
    if not stat_chips:
        stat_chips = '<span class="stat muted">stats update on game days</span>'
    team = esc(a.get("team")) or "—"
    return f"""
      <article class="card">
        <div class="card-top">
          <h3>{esc(a['name'])}</h3>
          <span class="pos">{esc(a.get('position'))}</span>
        </div>
        <div class="team">{team}</div>
        <div class="stats">{stat_chips}</div>
        <div class="home">Sacramento roots: {esc(a.get('hometown'))}</div>
      </article>"""


def render(athletes, updated):
    sections_html = ""
    for code, title in SECTIONS:
        group = [a for a in athletes if a.get("league") == code]
        if not group:
            continue
        cards = "".join(card_html(a) for a in group)
        sections_html += f"""
      <section>
        <h2>{esc(title)}</h2>
        <div class="grid">{cards}</div>
      </section>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sacramento's Pros</title>
<style>
  :root {{ --navy:{NAVY}; --brown:{BROWN}; }}
  * {{ box-sizing:border-box; }}
  body {{
    margin:0; padding:24px;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Lato,"Helvetica Neue",Arial,sans-serif;
    color:#222; background:#fff; line-height:1.45;
  }}
  header.page {{ border-bottom:3px solid var(--brown); padding-bottom:14px; margin-bottom:22px; }}
  header.page h1 {{ margin:0; color:var(--navy); font-size:1.7rem; letter-spacing:.2px; }}
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
  footer.page a {{ color:var(--brown); }}
</style>
</head>
<body>
  <header class="page">
    <h1>Sacramento's Pros</h1>
    <p>Active professional athletes with Sacramento-area roots — where they play now, and how they're doing.</p>
  </header>
  {sections_html}
  <footer class="page">
    Updated {esc(updated)}. Team &amp; stats via public sports data; roster curated by Michael Rehm.
    Where a stat line is still loading, it will populate as the season's games are played.
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
    for a in athletes:
        try:
            enrich(a)
        except Exception:
            a.setdefault("stats", [])  # never let one athlete break the build

    updated = datetime.datetime.now(datetime.timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
    page = render(athletes, updated)

    import os
    os.makedirs("public", exist_ok=True)
    with open("public/index.html", "w", encoding="utf-8") as f:
        f.write(page)
    print(f"Built public/index.html with {len(athletes)} athletes.")


if __name__ == "__main__":
    main()
