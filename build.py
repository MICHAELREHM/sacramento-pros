#!/usr/bin/env python3
"""
Build "Sacramento's Pros" — full, position-tailored box-score tables.

Pipeline:
  1. roster.json -> curated hometown athletes.
  2. Resolve each to an ESPN athlete id (active index, or pinned espnId).
  3. Current team  <- athlete endpoint.
     Full stat line <- /splits endpoint ("All Splits" season totals + category grouping).
  4. Render grouped, per-position tables into public/index.html.

Defensive: any failure -> athlete still listed (team + "awaiting stats").
Data-driven columns: whatever labels/categories ESPN returns for that player's
position become the columns, so pitchers, hitters, QBs, WRs, DBs, etc. each get
their own proper line. Nothing invented.
"""

import os
import re
import json
import html
import unicodedata
import datetime

import requests

SPORT_PATHS = {"mlb": ("baseball", "mlb"),
               "nfl": ("football", "nfl"),
               "nba": ("basketball", "nba")}
SECTIONS = [("mlb", "Baseball"), ("nfl", "Football"), ("nba", "Basketball")]

NAVY, BROWN = "#1F3A5F", "#8A5A1F"
TIMEOUT = 25
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (compatible; SacProsBot/1.0)"})
_INDEX_CACHE = {}


# --------------------------------------------------------------------------- ESPN
def _get_json(url):
    try:
        r = SESSION.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            print(f"  [warn] {r.status_code} {url[:70]}")
            return None
        return r.json()
    except Exception as e:
        print(f"  [warn] {e} {url[:70]}")
        return None


def norm(name):
    if not name:
        return ""
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"[.\-'’]", "", n.lower())
    return re.sub(r"\s+", " ", n).strip()


def league_index(league):
    if league in _INDEX_CACHE:
        return _INDEX_CACHE[league]
    sport, lg = SPORT_PATHS[league]
    data = _get_json(f"https://sports.core.api.espn.com/v3/sports/{sport}/{lg}"
                     f"/athletes?limit=20000&active=true")
    full_map, initial_map = {}, {}
    if data and isinstance(data.get("items"), list):
        for it in data["items"]:
            _id, full = it.get("id"), (it.get("fullName") or it.get("displayName") or "")
            if not _id or not full.strip():
                continue
            active, key = bool(it.get("active")), norm(full)
            if key and (key not in full_map or active):
                full_map[key] = _id
            p = key.split(" ")
            if len(p) >= 2:
                ik = p[0][0] + " " + p[-1]
                if ik not in initial_map or active:
                    initial_map[ik] = _id
        print(f"  [index] {league}: {len(full_map)} names")
    else:
        print(f"  [index] {league}: unavailable")
    _INDEX_CACHE[league] = (full_map, initial_map)
    return _INDEX_CACHE[league]


def resolve_id(name, league, espn_id):
    if espn_id and str(espn_id).strip():
        return str(espn_id).strip()
    full_map, initial_map = league_index(league)
    key = norm(name)
    if key in full_map:
        return full_map[key]
    p = key.split(" ")
    if len(p) >= 2 and (p[0][0] + " " + p[-1]) in initial_map:
        return initial_map[p[0][0] + " " + p[-1]]
    return None


def fetch_team(league, aid):
    sport, lg = SPORT_PATHS[league]
    data = _get_json(f"https://site.web.api.espn.com/apis/common/v3/sports/{sport}/{lg}"
                     f"/athletes/{aid}")
    if not data:
        return None
    ath = data.get("athlete") if isinstance(data.get("athlete"), dict) else data
    team = ath.get("team")
    if isinstance(team, dict):
        return team.get("displayName") or team.get("name")
    return None


def fetch_statgroups(league, aid):
    """Return (season_year, [(category_display, [(label, value), ...]), ...]) for the season total."""
    sport, lg = SPORT_PATHS[league]
    data = _get_json(f"https://site.web.api.espn.com/apis/common/v3/sports/{sport}/{lg}"
                     f"/athletes/{aid}/splits")
    if not data:
        return (None, [])
    labels = data.get("labels") or []
    cats = data.get("categories") or []
    season = None
    for flt in data.get("filters") or []:
        if flt.get("name") == "season":
            season = flt.get("value")
            break
    # Find the "All Splits" season-total row.
    total = None
    for sc in data.get("splitCategories") or []:
        if sc.get("name") == "split":
            for sp in sc.get("splits") or []:
                if sp.get("abbreviation") == "Any" or sp.get("displayName") == "All Splits":
                    total = sp.get("stats")
                    break
        if total:
            break
    if not total or not labels or len(total) != len(labels):
        return (season, [])
    # Slice the flat labels/stats into category groups by each category's count.
    groups, i = [], 0
    for c in cats:
        n = int(c.get("count") or 0)
        if n <= 0:
            continue
        pairs = list(zip(labels[i:i + n], total[i:i + n]))
        i += n
        if pairs:
            groups.append((c.get("displayName") or c.get("name") or "", pairs))
    return (season, groups)



# --- Curated "classic" stat lines per position (like a newspaper box score) ---
def _curate_category(pairs):
    """Given one ESPN category's (label,value) pairs, detect the position type and
    return (display_name, [curated (label,value)...]) or None to drop it."""
    d = {str(l).upper().strip(): v for l, v in pairs}
    keys = set(d)

    def pick(order):
        out = []
        for disp, aliases in order:
            for a in aliases:
                if a in d:
                    out.append((disp, d[a]))
                    break
        return out

    # Pitching: single combined W-L column, then the classic line.
    if (keys & {"ERA", "WHIP", "IP"}) or "W-L" in keys:
        cols = []
        wl = d.get("W-L")
        if not wl:
            wv = d.get("W") if d.get("W") is not None else d.get("WINS")
            lv = d.get("L") if d.get("L") is not None else d.get("LOSSES")
            if wv is not None and lv is not None:
                wl = f"{wv}-{lv}"
        if wl:
            cols.append(("W-L", wl))
        cols += pick([("IP", ["IP"]), ("H", ["H"]), ("R", ["R"]), ("ER", ["ER"]),
                      ("BB", ["BB"]), ("SO", ["SO", "K"]), ("ERA", ["ERA"]),
                      ("SV", ["SV", "SVO", "SAVES", "S"])])
        return ("Pitching", cols) if cols else None
    # Batting
    if (keys & {"AB", "OBP", "SLG", "OPS"}) or ("AVG" in keys and "HR" in keys):
        return ("Batting", pick([("AB", ["AB"]), ("R", ["R"]), ("H", ["H"]),
                                 ("HR", ["HR"]), ("RBI", ["RBI"]), ("SB", ["SB"]),
                                 ("BB", ["BB"]), ("SO", ["SO", "K"]),
                                 ("AVG", ["AVG", "BA"]), ("OPS", ["OPS"])]))
    # Passing
    if (keys & {"CMP", "RTG"}) or "CMP%" in keys:
        return ("Passing", pick([("CMP", ["CMP"]), ("ATT", ["ATT"]), ("YDS", ["YDS"]),
                                 ("TD", ["TD"]), ("INT", ["INT"]), ("RTG", ["RTG", "QBR"])]))
    # Rushing
    if "CAR" in keys:
        return ("Rushing", pick([("CAR", ["CAR"]), ("YDS", ["YDS"]),
                                 ("AVG", ["AVG"]), ("TD", ["TD"]), ("LNG", ["LNG"])]))
    # Receiving
    if "REC" in keys:
        return ("Receiving", pick([("REC", ["REC"]), ("TGT", ["TGTS", "TGT"]),
                                   ("YDS", ["YDS"]), ("AVG", ["AVG"]),
                                   ("TD", ["TD"]), ("LNG", ["LNG"])]))
    # Defense
    if keys & {"TCK", "TOT", "SOLO", "SACK", "PD", "FF"}:
        return ("Defense", pick([("TCK", ["TCK", "TOT", "TACK"]), ("SOLO", ["SOLO"]),
                                 ("SACK", ["SACK", "SCK"]), ("INT", ["INT"]),
                                 ("PD", ["PD"]), ("FF", ["FF"])]))
    # Basketball
    if keys & {"PTS", "REB", "AST", "PPG", "RPG", "APG"}:
        return ("Per Game", pick([("GP", ["GP"]), ("PTS", ["PTS", "PPG"]),
                                  ("REB", ["REB", "RPG"]), ("AST", ["AST", "APG"]),
                                  ("STL", ["STL", "SPG"]), ("BLK", ["BLK", "BPG"]),
                                  ("FG%", ["FG%"]), ("3P%", ["3P%"]), ("FT%", ["FT%"])]))
    # Offensive linemen / players with only game counts -> Games Played / Started
    if keys & {"GP", "GS", "G"}:
        cols = pick([("GP", ["GP", "G"]), ("GS", ["GS"])])
        if cols:
            return ("Games", cols)
    return None  # unrecognized category -> drop (keeps tables clean)


def curate(statgroups):
    out = []
    for cat, pairs in statgroups:
        c = _curate_category(pairs)
        if c and c[1]:
            out.append(c)
    return out


def _extract_record(statgroups):
    """Pull a pitcher's W-L record (e.g. '9-6') to fold into the name line."""
    for _cat, pairs in statgroups:
        d = {str(l).upper().strip(): v for l, v in pairs}
        if "W-L" in d and "-" in str(d["W-L"]):
            return d["W-L"]
        if "W" in d and "L" in d:
            return f"{d['W']}-{d['L']}"
    return None


# ------------------------------------------------------------------ MLB Stats API
# Baseball uses MLB's official first-party feed (statsapi.mlb.com) for the fullest,
# most authoritative hitter/pitcher lines. Football/basketball stay on ESPN.
MLB_BASE = "https://statsapi.mlb.com/api/v1"
MLB_YEAR = datetime.datetime.now(datetime.timezone.utc).year
_MLB_INDEX = {}
PITCH_POS = {"SP", "RP", "RHP", "LHP", "P", "RP/CL", "CL"}
# Levels to search, highest first: MLB, Triple-A, Double-A, High-A, Single-A, Rookie
MLB_LEVELS = [(1, "MLB"), (11, "AAA"), (12, "AA"), (13, "A+"), (14, "A"), (16, "Rk")]


def mlb_index(year):
    """Name -> MLB person id across the majors AND minor leagues (one global id per player)."""
    if year in _MLB_INDEX:
        return _MLB_INDEX[year]
    m = {}
    for sid, level in MLB_LEVELS:
        data = _get_json(f"{MLB_BASE}/sports/{sid}/players?season={year}")
        if not data:
            continue
        for p in data.get("people", []):
            nm, pid = norm(p.get("fullName") or ""), p.get("id")
            if nm and pid:
                m.setdefault(nm, pid)
    print(f"  [mlb-index] {year}: {len(m)} players (majors + minors)")
    _MLB_INDEX[year] = m
    return m


def mlb_resolve(name, pinned):
    if pinned and str(pinned).strip():
        return str(pinned).strip()
    key = norm(name)
    for yr in (MLB_YEAR, MLB_YEAR - 1):
        m = mlb_index(yr)
        if key in m:
            return str(m[key])
        parts = key.split(" ")
        if len(parts) >= 2:
            for k, v in m.items():
                kp = k.split(" ")
                if kp[-1] == parts[-1] and kp[0][:1] == parts[0][:1]:
                    return str(v)
        if m:  # index existed but no match; still try prior year
            continue
    return None


def _pct(v):
    v = str(v) if v is not None else None
    if v and v.startswith("0."):
        return v[1:]
    return v


def _mlb_build(person, is_pitcher):
    """Turn an MLB people[] record (with hydrated season stats) into our format."""
    team = None
    ct = person.get("currentTeam")
    if isinstance(ct, dict):
        team = ct.get("name")
    hit = pit = None
    season = None
    split_team = None
    for s in person.get("stats") or []:
        grp = (s.get("group") or {}).get("displayName")
        splits = s.get("splits") or []
        if not splits:
            continue
        sp0 = splits[0]
        st = sp0.get("stat") or {}
        season = sp0.get("season") or season
        tm = sp0.get("team")
        if isinstance(tm, dict) and tm.get("name"):
            split_team = tm.get("name")
        if grp == "pitching":
            pit = st
        elif grp == "hitting":
            hit = st
    team = team or split_team
    groups = []
    if is_pitcher and pit:
        w, l = pit.get("wins"), pit.get("losses")
        wl = f"{w}-{l}" if w is not None and l is not None else None
        line = [("W-L", wl), ("IP", pit.get("inningsPitched")), ("H", pit.get("hits")),
                ("R", pit.get("runs")), ("ER", pit.get("earnedRuns")),
                ("BB", pit.get("baseOnBalls")), ("SO", pit.get("strikeOuts")),
                ("ERA", pit.get("era")), ("SV", pit.get("saves"))]
        groups = [("Pitching", [(k, v) for k, v in line if v is not None])]
    elif hit:
        line = [("AB", hit.get("atBats")), ("R", hit.get("runs")), ("H", hit.get("hits")),
                ("HR", hit.get("homeRuns")), ("RBI", hit.get("rbi")),
                ("SB", hit.get("stolenBases")), ("BB", hit.get("baseOnBalls")),
                ("SO", hit.get("strikeOuts")), ("AVG", _pct(hit.get("avg"))),
                ("OPS", _pct(hit.get("ops")))]
        groups = [("Batting", [(k, v) for k, v in line if v is not None])]
    return team, (str(season) if season else None), groups


def mlb_stats(pid, year, is_pitcher):
    """Find the player's season line at the highest level they actually played."""
    for sid, level in MLB_LEVELS:
        data = _get_json(f"{MLB_BASE}/people/{pid}?hydrate=currentTeam,"
                         f"stats(type=[season],season={year},sportId={sid})")
        people = (data or {}).get("people") or []
        if not people:
            continue
        team, season, groups = _mlb_build(people[0], is_pitcher)
        if groups:
            return team, season, groups, level
    return (None, None, [], None)


def enrich_mlb(a):
    pid = mlb_resolve(a.get("name", ""), a.get("mlbId"))
    if not pid:
        print(f"  [miss] {a.get('name')} — no MLB id")
        return a
    is_pitcher = (a.get("position", "").upper() in PITCH_POS)
    team, season, groups, level = mlb_stats(pid, MLB_YEAR, is_pitcher)
    if not groups:  # nothing this year -> try last year
        team, season, groups, level = mlb_stats(pid, MLB_YEAR - 1, is_pitcher)
    if team:
        a["team"] = team
    base_season = season or str(MLB_YEAR)
    # Mark the level in the Season box when it's not the majors (e.g. "2026 AAA").
    a["season"] = base_season if level in (None, "MLB") else f"{base_season} {level}"
    a["statgroups"] = groups
    n = sum(len(p) for _, p in groups)
    lvl = level or "-"
    print(f"  [mlb]  {a.get('name')} -> id {pid}, {lvl}, {n} stats, {a.get('team')}")
    return a


def enrich(a):
    league = a.get("league")
    a.setdefault("statgroups", [])
    if league == "mlb":
        return enrich_mlb(a)
    if league not in SPORT_PATHS:
        return a
    aid = resolve_id(a.get("name", ""), league, a.get("espnId"))
    if not aid:
        print(f"  [miss] {a.get('name')} — no id")
        return a
    team = fetch_team(league, aid)
    if team:
        a["team"] = team
    season, raw = fetch_statgroups(league, aid)
    a["season"] = season
    a["record"] = _extract_record(raw)
    a["statgroups"] = curate(raw)
    ncols = sum(len(p) for _, p in a["statgroups"])
    print(f"  [ok]   {a.get('name')} -> id {aid}, {ncols} stats")
    return a


# --------------------------------------------------------------------------- render
def esc(s):
    return html.escape(str(s if s is not None else ""))


def signature(a):
    return tuple((cat, tuple(l for l, _ in pairs)) for cat, pairs in (a.get("statgroups") or []))


def _name_cell(a):
    rec = ""
    bits = []
    if a.get("position"):
        bits.append(esc(a.get("position")))
    if a.get("team"):
        bits.append(esc(a.get("team")))
    if a.get("hometown"):
        bits.append(esc(a.get("hometown")))
    sub = " · ".join(bits)
    return (f'<span class="player">{esc(a["name"])}{rec}</span>'
            f'<span class="roots">{sub}</span>')


def _season_cell(a):
    s = a.get("season")
    return f'<td class="id season">{esc(s) if s else "—"}</td>'


def render_table(members, sig, awaiting=False):
    # Awaiting / no stats
    if awaiting or sum(len(labels) for _, labels in sig) == 0:
        rows = "".join(
            f'<tr><td class="id">{_name_cell(a)}</td>{_season_cell(a)}'
            f'<td class="note">Awaiting current-season stats</td></tr>'
            for a in members)
        thead = ("<thead><tr><th class='id'>Player</th>"
                 "<th class='id'>Season</th><th class='note'>&nbsp;</th></tr></thead>")
        return f'<table class="box">{thead}<tbody>{rows}</tbody></table>'

    multi = len(sig) > 1
    if multi:
        top = '<th class="id" rowspan="2">Player</th><th class="id" rowspan="2">Season</th>'
        for cat, labels in sig:
            top += f'<th class="grp" colspan="{len(labels)}">{esc(cat)}</th>'
        sub = "".join(f'<th class="num">{esc(l)}</th>' for _, labels in sig for l in labels)
        thead = f"<thead><tr>{top}</tr><tr>{sub}</tr></thead>"
        cap = ""
    else:
        cat, labels = sig[0]
        head = "".join(f'<th class="num">{esc(l)}</th>' for l in labels)
        thead = f"<thead><tr><th class='id'>Player</th><th class='id'>Season</th>{head}</tr></thead>"
        cap = f"<caption>{esc(cat)}</caption>" if cat else ""

    rows = ""
    for a in members:
        cells = "".join(f'<td class="num">{esc(v)}</td>'
                        for _, pairs in (a.get("statgroups") or []) for _, v in pairs)
        rows += f'<tr><td class="id">{_name_cell(a)}</td>{_season_cell(a)}{cells}</tr>'
    return f'<table class="box">{cap}{thead}<tbody>{rows}</tbody></table>'


def render(athletes, updated):
    body = ""
    for code, title in SECTIONS:
        roster = [a for a in athletes if a.get("league") == code]
        if not roster:
            continue
        body += f"<section><h2>{esc(title)}</h2>"
        groups = {}
        for a in roster:
            groups.setdefault(signature(a), []).append(a)
        for sig in sorted((g for g in groups if g), key=lambda s: -len(groups[s])):
            body += render_table(groups[sig], sig)
        if () in groups:
            body += render_table(groups[()], (), awaiting=True)
        body += "</section>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sacramento's Pros</title>
<style>
  :root {{
    --bg:#ffffff; --fg:#232323; --muted:#8a8a8a; --line:#ececec; --row-alt:#f7f8fa;
    --accent:#8A5A1F; --title:#1F3A5F; --thead-bg:#1F3A5F; --thead-bg2:#274a73; --thead-fg:#ffffff;
    --btn-bg:#1F3A5F; --btn-fg:#ffffff;
  }}
  html[data-theme="dark"] {{
    --bg:#12161c; --fg:#e7e9ec; --muted:#9aa3ad; --line:#2a313a; --row-alt:#181f28;
    --accent:#cf9d5b; --title:#8fb2e0; --thead-bg:#1b2836; --thead-bg2:#243449; --thead-fg:#e9edf2;
    --btn-bg:#243449; --btn-fg:#e9edf2;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:20px; color:var(--fg); background:var(--bg); line-height:1.3;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Lato,"Helvetica Neue",Arial,sans-serif;
    transition:background .2s ease, color .2s ease; }}
  header.page {{ border-bottom:3px solid var(--accent); padding-bottom:12px; margin-bottom:18px; }}
  .head-row {{ display:flex; justify-content:space-between; align-items:flex-start; gap:16px; flex-wrap:wrap; }}
  header.page h1 {{ margin:0; color:var(--title); font-size:1.6rem; }}
  header.page p {{ margin:6px 0 0; color:var(--muted); font-size:.92rem; max-width:62ch; }}
  .head-meta {{ text-align:right; white-space:nowrap; }}
  .updated-top {{ color:var(--muted); font-size:.76rem; margin-top:8px; }}
  .themebtn {{ cursor:pointer; border:1px solid var(--line); background:var(--btn-bg); color:var(--btn-fg);
    font-size:.76rem; padding:6px 12px; border-radius:6px; font-weight:600; }}
  .themebtn:hover {{ opacity:.9; }}
  section {{ margin-bottom:24px; }}
  h2 {{ color:var(--title); border-left:5px solid var(--accent); padding-left:9px;
    margin:20px 0 10px; font-size:1.25rem; }}
  table.box {{ border-collapse:collapse; width:auto; max-width:100%; margin:0 0 18px; font-size:.85rem; }}
  caption {{ text-align:left; caption-side:top; font-weight:700; color:var(--accent);
    padding:2px 2px 6px; font-size:.72rem; text-transform:uppercase; letter-spacing:.6px; }}
  thead th {{ background:var(--thead-bg); color:var(--thead-fg); font-weight:600; padding:5px 10px;
    text-align:right; font-size:.72rem; text-transform:uppercase; letter-spacing:.3px; white-space:nowrap; }}
  thead th.id {{ text-align:left; }}
  thead th.grp {{ text-align:center; border-left:1px solid var(--thead-bg2); border-right:1px solid var(--thead-bg2); background:var(--thead-bg2); }}
  tbody td {{ padding:6px 10px; border-bottom:1px solid var(--line); vertical-align:top; text-align:right; white-space:nowrap; }}
  tbody td.id {{ text-align:left; padding-right:22px; white-space:normal; max-width:230px; }}
  tbody td.note {{ text-align:left; color:var(--muted); font-style:italic; }}
  tbody tr:nth-child(even) {{ background:var(--row-alt); }}
  th.num, td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .player {{ font-weight:700; color:var(--title); display:block; line-height:1.15; white-space:normal; }}
  .roots {{ display:block; font-weight:400; font-size:.74rem; color:var(--muted); margin-top:1px; white-space:normal; }}
  td.season {{ color:var(--muted); font-variant-numeric:tabular-nums; font-size:.82rem; padding-right:16px; }}
  footer.page {{ margin-top:22px; padding-top:12px; border-top:1px solid var(--line); color:var(--muted); font-size:.76rem; }}
  .legend {{ margin-bottom:8px; color:var(--muted); }}
  .legend b {{ color:var(--title); font-weight:700; }}
</style>
<script>
  (function(){{
    var t=null; try {{ t=localStorage.getItem('sacpros-theme'); }} catch(e){{}}
    if(!t) {{ t=(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches)?'dark':'light'; }}
    document.documentElement.setAttribute('data-theme', t);
  }})();
</script>
</head>
<body>
  <header class="page">
    <div class="head-row">
      <div>
        <h1>Sacramento's Pros</h1>
        <p>Active professional athletes with Sacramento-area roots — where they play now, and how they're doing.</p>
      </div>
      <div class="head-meta">
        <button id="themebtn" class="themebtn" onclick="toggleTheme()">Dark mode</button>
        <div class="updated-top">Updated {esc(updated)}</div>
      </div>
    </div>
  </header>
  {body}
  <footer class="page">
    <div class="legend">Level (baseball): <b>AAA</b> Triple-A · <b>AA</b> Double-A · <b>A+</b> High-A · <b>A</b> Single-A · <b>Rk</b> Rookie. No tag = Major Leagues.</div>
    Updated {esc(updated)}. Season stat lines &amp; current team via public sports data; roster curated by Michael Rehm.
    Off-season lines reflect the most recent completed season.
  </footer>
  <script>
    function toggleTheme(){{
      var el=document.documentElement;
      var cur=el.getAttribute('data-theme')==='dark'?'light':'dark';
      el.setAttribute('data-theme',cur);
      try {{ localStorage.setItem('sacpros-theme',cur); }} catch(e){{}}
      var b=document.getElementById('themebtn');
      if(b) b.textContent = cur==='dark' ? 'Light mode' : 'Dark mode';
      if(typeof _sacProsPostHeight==='function') _sacProsPostHeight();
    }}
    window.addEventListener('load', function(){{
      var b=document.getElementById('themebtn');
      if(b) b.textContent = document.documentElement.getAttribute('data-theme')==='dark' ? 'Light mode' : 'Dark mode';
    }});
  </script>
  <script>
    // Tell a parent page (e.g. WordPress embed) how tall this content is,
    // so the iframe can auto-size and never show an inner scrollbar.
    function _sacProsPostHeight() {{
      var h = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
      try {{ parent.postMessage({{ sacProsHeight: h }}, "*"); }} catch (e) {{}}
    }}
    window.addEventListener("load", _sacProsPostHeight);
    window.addEventListener("resize", _sacProsPostHeight);
    setTimeout(_sacProsPostHeight, 400);
  </script>
</body>
</html>
"""


def main():
    with open("roster.json", "r", encoding="utf-8") as f:
        athletes = json.load(f).get("athletes", [])
    print(f"Enriching {len(athletes)} athletes...")
    for a in athletes:
        try:
            enrich(a)
        except Exception as e:
            print(f"  [error] {a.get('name')}: {e}")
            a.setdefault("statgroups", [])
    updated = datetime.datetime.now(datetime.timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
    os.makedirs("public", exist_ok=True)
    with open("public/index.html", "w", encoding="utf-8") as f:
        f.write(render(athletes, updated))
    print(f"Built public/index.html with {len(athletes)} athletes.")


if __name__ == "__main__":
    main()
