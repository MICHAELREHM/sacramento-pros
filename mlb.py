#!/usr/bin/env python3
"""
Build an MLB Standings + League Leaders page (public/mlb.html) from MLB's
official Stats API (statsapi.mlb.com). Self-updating via the same daily
GitHub Actions workflow. Brought to you by Sacramento Real Estate Agent
Michael Rehm.
"""

import os
import html
import datetime

import requests

try:
    from zoneinfo import ZoneInfo
    PT = ZoneInfo("America/Los_Angeles")
except Exception:
    PT = None

MLB_BASE = "https://statsapi.mlb.com/api/v1"
YEAR = datetime.datetime.now(datetime.timezone.utc).year
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (compatible; SacProsBot/1.0)"})

# Division id -> name
DIVISIONS = {201: "AL East", 202: "AL Central", 200: "AL West",
             204: "NL East", 205: "NL Central", 203: "NL West"}
AL_ORDER = [201, 202, 200]
NL_ORDER = [204, 205, 203]
# code, display name, division ids, MLB leagueId
LEAGUES = [("AL", "American League", AL_ORDER, 103),
           ("NL", "National League", NL_ORDER, 104)]
AL_DIVS = set(AL_ORDER)


def team_leagues(standings):
    m = {}
    for did, rows in standings.items():
        lg = "AL" if did in AL_DIVS else "NL"
        for r in rows:
            if r.get("team"):
                m[r["team"]] = lg
    return m


def split_by_league(games, tl):
    out = {"AL": [], "NL": []}
    for g in games:
        al = tl.get(g["away"]["name"])
        hl = tl.get(g["home"]["name"])
        leagues = [x for x in (al, hl) if x]
        if al and hl and al != hl:  # interleague -> show in both, tagged
            g = dict(g)
            g["interleague"] = True
            out["AL"].append(g)
            out["NL"].append(g)
        elif leagues:
            out[leagues[0]].append(g)
        else:  # unknown -> don't lose it
            out["AL"].append(g)
    return out

HIT_CATS = [("battingAverage", "AVG"), ("homeRuns", "HR"), ("runsBattedIn", "RBI"),
            ("onBasePlusSlugging", "OPS"), ("stolenBases", "SB")]
PIT_CATS = [("earnedRunAverage", "ERA"), ("wins", "W"), ("strikeouts", "SO"),
            ("saves", "SV")]

REHM_NAME = "Michael Rehm"
REHM_DRE = "CA DRE #02143896"
REHM_PHONE = "916-469-7041"


def esc(s):
    return html.escape(str(s if s is not None else ""))


def get(url):
    try:
        r = SESSION.get(url, timeout=25)
        if r.status_code == 200:
            return r.json()
        print(f"  [warn] {r.status_code} {url[:80]}")
    except Exception as e:
        print(f"  [warn] {e} {url[:80]}")
    return None


def _pct(v):
    v = str(v) if v is not None else ""
    return v[1:] if v.startswith("0.") else v


# ------------------------------------------------------------------ standings
def fetch_standings(year):
    data = get(f"{MLB_BASE}/standings?leagueId=103,104&season={year}"
               f"&standingsTypes=regularSeason&hydrate=team")
    out = {}
    if not data:
        return out
    for rec in data.get("records", []):
        did = (rec.get("division") or {}).get("id")
        rows = []
        for tr in rec.get("teamRecords", []):
            splits = {s.get("type"): s for s in
                      ((tr.get("records") or {}).get("splitRecords") or [])}

            def wl(s):
                return f"{s.get('wins')}-{s.get('losses')}" if s else "—"

            rows.append({
                "team": (tr.get("team") or {}).get("name"),
                "w": tr.get("wins"), "l": tr.get("losses"),
                "pct": _pct(tr.get("winningPercentage")),
                "gb": tr.get("gamesBack") or "—",
                "strk": (tr.get("streak") or {}).get("streakCode", "—"),
                "l10": wl(splits.get("lastTen")),
                "home": wl(splits.get("home")),
                "away": wl(splits.get("away")),
                "clinch": tr.get("clinchIndicator") or "",
            })
        if did:
            out[did] = rows
    return out


# ------------------------------------------------------------------ leaders
def fetch_leaders(cat, group, year, league_id=None, limit=5):
    url = (f"{MLB_BASE}/stats/leaders?leaderCategories={cat}"
           f"&statGroup={group}&season={year}&sportId=1&limit={limit}")
    if league_id:
        url += f"&leagueId={league_id}"
    data = get(url)
    out = []
    if not data:
        return out
    lls = data.get("leagueLeaders") or []
    if lls:
        for ld in lls[0].get("leaders", []):
            out.append({
                "rank": ld.get("rank"),
                "name": (ld.get("person") or {}).get("fullName"),
                "team": (ld.get("team") or {}).get("teamName")
                        or (ld.get("team") or {}).get("name"),
                "value": ld.get("value"),
            })
    return out


# ------------------------------------------------------------------ scoreboard
_PITCH_CACHE = {}


def _fmt_time(iso):
    if not iso:
        return "TBD"
    try:
        dt = datetime.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc)
        if PT:
            dt = dt.astimezone(PT)
        t = dt.strftime("%I:%M %p").lstrip("0")
        return f"{t} PT" if PT else f"{t} UTC"
    except Exception:
        return "TBD"


def pitcher_line(pid, year):
    """Return 'W-L, ERA' for a probable pitcher (cached)."""
    if not pid:
        return ""
    if pid in _PITCH_CACHE:
        return _PITCH_CACHE[pid]
    data = get(f"{MLB_BASE}/people/{pid}?hydrate=stats(group=[pitching],"
               f"type=[season],season={year},sportId=1)")
    line = ""
    try:
        st = data["people"][0]["stats"][0]["splits"][0]["stat"]
        line = f"{st.get('wins', 0)}-{st.get('losses', 0)}, {st.get('era', '-.--')} ERA"
    except Exception:
        line = ""
    _PITCH_CACHE[pid] = line
    return line


def fetch_schedule(date_str):
    data = get(f"{MLB_BASE}/schedule?sportId=1&date={date_str}"
               f"&hydrate=probablePitcher,decisions,linescore,team")
    games = []
    if not data:
        return games
    for d in data.get("dates", []):
        for g in d.get("games", []):
            aw, hm = g["teams"]["away"], g["teams"]["home"]
            ls = g.get("linescore") or {}
            lst = (ls.get("teams") or {})
            dec = g.get("decisions") or {}

            def side(x, key):
                t = x.get("team") or {}
                return {
                    "name": t.get("name"), "abbr": t.get("abbreviation"),
                    "score": x.get("score"),
                    "rec": (x.get("leagueRecord") or {}),
                    "prob": (x.get("probablePitcher") or {}),
                    "r": (lst.get(key) or {}).get("runs"),
                    "h": (lst.get(key) or {}).get("hits"),
                    "e": (lst.get(key) or {}).get("errors"),
                }

            games.append({
                "pk": g.get("gamePk"),
                "state": (g.get("status") or {}).get("abstractGameState"),
                "detailed": (g.get("status") or {}).get("detailedState"),
                "time": _fmt_time(g.get("gameDate")),
                "away": side(aw, "away"),
                "home": side(hm, "home"),
                "win": (dec.get("winner") or {}).get("fullName"),
                "lose": (dec.get("loser") or {}).get("fullName"),
                "save": (dec.get("save") or {}).get("fullName"),
            })
    return games


# ------------------------------------------------------------------ box scores
def fetch_boxscore(pk):
    return get(f"{MLB_BASE}/game/{pk}/boxscore")


def _box_batting(t):
    players = t.get("players", {})
    head = ("<tr><th class='id'>Batters</th><th>AB</th><th>R</th><th>H</th>"
            "<th>RBI</th><th>BB</th><th>SO</th><th>AVG</th></tr>")
    body = ""
    tot = {"ab": 0, "r": 0, "h": 0, "bi": 0, "bb": 0, "so": 0}
    for pid in t.get("batters", []):
        p = players.get(f"ID{pid}", {})
        b = (p.get("stats") or {}).get("batting") or {}
        ab = b.get("atBats")
        if ab is None:
            continue
        name = (p.get("person") or {}).get("fullName", "")
        pos = (p.get("position") or {}).get("abbreviation", "")
        starter = str(p.get("battingOrder") or "").endswith("00")
        avg = ((p.get("seasonStats") or {}).get("batting") or {}).get("avg", "")
        v = {"ab": ab, "r": b.get("runs", 0), "h": b.get("hits", 0),
             "bi": b.get("rbi", 0), "bb": b.get("baseOnBalls", 0),
             "so": b.get("strikeOuts", 0)}
        for k in tot:
            tot[k] += v[k] or 0
        cls = "id" if starter else "id sub"
        body += (f"<tr><td class='{cls}'>{esc(name)} {esc(pos)}</td>"
                 f"<td>{esc(v['ab'])}</td><td>{esc(v['r'])}</td><td>{esc(v['h'])}</td>"
                 f"<td>{esc(v['bi'])}</td><td>{esc(v['bb'])}</td><td>{esc(v['so'])}</td>"
                 f"<td>{esc(avg)}</td></tr>")
    body += (f"<tr class='tot'><td class='id'>Totals</td><td>{tot['ab']}</td>"
             f"<td>{tot['r']}</td><td>{tot['h']}</td><td>{tot['bi']}</td>"
             f"<td>{tot['bb']}</td><td>{tot['so']}</td><td></td></tr>")
    return f"<table class='bx'><thead>{head}</thead><tbody>{body}</tbody></table>"


def _box_pitching(t):
    players = t.get("players", {})
    head = ("<tr><th class='id'>Pitchers</th><th>IP</th><th>H</th><th>R</th>"
            "<th>ER</th><th>BB</th><th>SO</th><th>NP</th><th>ERA</th></tr>")
    body = ""
    for pid in t.get("pitchers", []):
        p = players.get(f"ID{pid}", {})
        pit = (p.get("stats") or {}).get("pitching") or {}
        if not pit:
            continue
        name = (p.get("person") or {}).get("fullName", "")
        note = p.get("note") or ""
        era = ((p.get("seasonStats") or {}).get("pitching") or {}).get("era", "")
        np = pit.get("numberOfPitches") or pit.get("pitchesThrown") or ""
        body += (f"<tr><td class='id'>{esc(name)} {esc(note)}</td>"
                 f"<td>{esc(pit.get('inningsPitched'))}</td><td>{esc(pit.get('hits'))}</td>"
                 f"<td>{esc(pit.get('runs'))}</td><td>{esc(pit.get('earnedRuns'))}</td>"
                 f"<td>{esc(pit.get('baseOnBalls'))}</td><td>{esc(pit.get('strikeOuts'))}</td>"
                 f"<td>{esc(np)}</td><td>{esc(era)}</td></tr>")
    return f"<table class='bx'><thead>{head}</thead><tbody>{body}</tbody></table>"


def _box_notes(t):
    out = ""
    for sec in t.get("info", []):
        for f in sec.get("fieldList", []):
            lbl, val = f.get("label", ""), f.get("value", "")
            if lbl or val:
                out += f"<div class='bxnote'><b>{esc(lbl)}:</b> {esc(val)}</div>"
    return out


def render_boxscore(box):
    if not box or "teams" not in box:
        return ""
    parts = ""
    for side in ("away", "home"):
        t = box["teams"].get(side) or {}
        nm = (t.get("team") or {}).get("name", "")
        parts += (f"<div class='bxteam'><h4>{esc(nm)}</h4>"
                  f"{_box_batting(t)}{_box_pitching(t)}"
                  f"<div class='bxnotes'>{_box_notes(t)}</div></div>")
    gi = ""
    for f in box.get("info", []):
        lbl, val = f.get("label", ""), f.get("value", "")
        if lbl:
            gi += f"<span class='gi'><b>{esc(lbl)}</b> {esc(val)}</span> "
    if gi:
        parts += f"<div class='gameinfo'>{gi}</div>"
    return f"<div class='boxscore'>{parts}</div>"


# ------------------------------------------------------------------ render
def render_scoreboard(title, games, year, mode):
    if not games:
        return (f"<section><h2>{esc(title)}</h2>"
                f"<p class='empty'>No games.</p></section>")
    cards = ""
    for g in games:
        a, h = g["away"], g["home"]
        il = " <span class='il'>Interleague</span>" if g.get("interleague") else ""
        if mode == "final":
            aw_win = (a["score"] or 0) > (h["score"] or 0)
            aw = (f"<span class='{'win' if aw_win else ''}'>"
                  f"{esc(a['name'])} <b>{esc(a['score'])}</b></span>")
            hw = (f"<span class='{'win' if not aw_win else ''}'>"
                  f"{esc(h['name'])} <b>{esc(h['score'])}</b></span>")
            rhe = (f"<span class='rhe'>R-H-E: {esc(a['abbr'])} "
                   f"{esc(a['r'])}-{esc(a['h'])}-{esc(a['e'])}, "
                   f"{esc(h['abbr'])} {esc(h['r'])}-{esc(h['h'])}-{esc(h['e'])}</span>")
            dec = []
            if g["win"]:
                dec.append(f"W: {esc(g['win'])}")
            if g["lose"]:
                dec.append(f"L: {esc(g['lose'])}")
            if g["save"]:
                dec.append(f"S: {esc(g['save'])}")
            decline = (f"<div class='dec'>{' · '.join(dec)}</div>" if dec else "")
            note = "" if g["state"] == "Final" else f" <em>({esc(g['detailed'])})</em>"
            inner = (f"{decline}<div class='meta'>{rhe}</div>")
            box = g.get("box")
            if box:
                inner += render_boxscore(box)
                cards += (f"<details class='game'><summary>{aw} at {hw}{note}{il}</summary>"
                          f"<div class='boxwrap'>{inner}</div></details>")
            else:
                cards += (f"<div class='game'><div class='score'>{aw} at {hw}{note}{il}</div>"
                          f"{inner}</div>")
        else:  # upcoming
            ap = pitcher_line((a["prob"] or {}).get("id"), year)
            hp = pitcher_line((h["prob"] or {}).get("id"), year)
            apn = (a["prob"] or {}).get("fullName") or "TBD"
            hpn = (h["prob"] or {}).get("fullName") or "TBD"
            ap = f" ({ap})" if ap else ""
            hp = f" ({hp})" if hp else ""
            cards += (f"<div class='game'>"
                      f"<div class='score'>{esc(a['name'])} at {esc(h['name'])}{il}"
                      f"<span class='gtime'>{esc(g['time'])}</span></div>"
                      f"<div class='meta'>{esc(apn)}{ap} vs. {esc(hpn)}{hp}</div></div>")
    return f"<section><h2>{esc(title)}</h2><div class='games'>{cards}</div></section>"


def standings_table(div_id, rows):
    name = DIVISIONS.get(div_id, "Division")
    head = ("<tr><th class='id'>Team</th><th>W</th><th>L</th><th>Pct</th>"
            "<th>GB</th><th>L10</th><th>Strk</th><th>Home</th><th>Away</th></tr>")
    body = ""
    for i, r in enumerate(rows):
        lead = " lead" if i == 0 else ""
        clinch = f" <span class='clinch'>{esc(r['clinch'])}</span>" if r["clinch"] else ""
        body += (
            f"<tr class='row{lead}'>"
            f"<td class='id'>{esc(r['team'])}{clinch}</td>"
            f"<td>{esc(r['w'])}</td><td>{esc(r['l'])}</td>"
            f"<td>{esc(r['pct'])}</td><td>{esc(r['gb'])}</td>"
            f"<td>{esc(r['l10'])}</td><td>{esc(r['strk'])}</td>"
            f"<td>{esc(r['home'])}</td><td>{esc(r['away'])}</td></tr>"
        )
    return (f"<table class='box'><caption>{esc(name)}</caption>"
            f"<thead>{head}</thead><tbody>{body}</tbody></table>")


def leader_card(title, rows, unit=""):
    items = ""
    for r in rows:
        team = f" <span class='lteam'>{esc(r['team'])}</span>" if r.get("team") else ""
        items += (f"<li><span class='lrank'>{esc(r['rank'])}</span>"
                  f"<span class='lname'>{esc(r['name'])}{team}</span>"
                  f"<span class='lval'>{esc(r['value'])}</span></li>")
    return (f"<div class='leader'><h3>{esc(title)}</h3><ol>{items}</ol></div>")


def render(standings, leaders, games, updated, year):
    body = ""
    for code, name, divs, _lg in LEAGUES:
        st = "".join(standings_table(d, standings.get(d, []))
                     for d in divs if standings.get(d))
        L = leaders.get(code, {})
        hit_cards = "".join(leader_card(lbl, L.get("hit", {}).get(key, []))
                            for key, lbl in HIT_CATS)
        pit_cards = "".join(leader_card(lbl, L.get("pit", {}).get(key, []))
                            for key, lbl in PIT_CATS)
        G = games.get(code, {})
        body += (
            f"<section class='league'>"
            f"<div class='league-title'>{esc(name)}</div>"
            f"<h2>Standings</h2><div class='cols3'>{st}</div>"
            f"<h2>Stat Leaders</h2>"
            f"<h3 class='lg'>Hitting</h3><div class='leaders'>{hit_cards}</div>"
            f"<h3 class='lg'>Pitching</h3><div class='leaders'>{pit_cards}</div>"
            + render_scoreboard("Yesterday's Scores", G.get("yesterday", []), year, "final")
            + render_scoreboard("Today's Games", G.get("today", []), year, "upcoming")
            + render_scoreboard("Tomorrow's Games", G.get("tomorrow", []), year, "upcoming")
            + "</section>"
        )

    page = PAGE
    page = page.replace("{{UPDATED}}", esc(updated))
    page = page.replace("{{BODY}}", body)
    page = page.replace("{{NAME}}", esc(REHM_NAME))
    page = page.replace("{{DRE}}", esc(REHM_DRE))
    page = page.replace("{{PHONE}}", esc(REHM_PHONE))
    return page


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MLB Standings & Stats</title>
<style>
  :root {
    --bg:#ffffff; --fg:#232323; --muted:#8a8a8a; --line:#ececec; --row-alt:#f7f8fa;
    --accent:#8A5A1F; --title:#1F3A5F; --thead-bg:#1F3A5F; --thead-fg:#ffffff;
    --card:#f7f8fa; --lead:#fbf4e9; --btn-bg:#1F3A5F; --btn-fg:#ffffff;
  }
  html[data-theme="dark"] {
    --bg:#12161c; --fg:#e7e9ec; --muted:#9aa3ad; --line:#2a313a; --row-alt:#181f28;
    --accent:#cf9d5b; --title:#8fb2e0; --thead-bg:#1b2836; --thead-fg:#e9edf2;
    --card:#171d25; --lead:#20242c; --btn-bg:#243449; --btn-fg:#e9edf2;
  }
  * { box-sizing:border-box; }
  body { margin:0; padding:20px; color:var(--fg); background:var(--bg); line-height:1.3;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Lato,"Helvetica Neue",Arial,sans-serif;
    transition:background .2s ease, color .2s ease; }
  header.page { border-bottom:3px solid var(--accent); padding-bottom:12px; margin-bottom:18px; }
  .head-row { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; flex-wrap:wrap; }
  header.page h1 { margin:0; color:var(--title); font-size:1.7rem; }
  .byline { margin:6px 0 0; color:var(--accent); font-weight:700; font-size:.95rem; }
  .head-meta { text-align:right; white-space:nowrap; }
  .updated-top { color:var(--muted); font-size:.76rem; margin-top:8px; }
  .themebtn { cursor:pointer; border:1px solid var(--line); background:var(--btn-bg); color:var(--btn-fg);
    font-size:.76rem; padding:6px 12px; border-radius:6px; font-weight:600; }
  .themebtn:hover { opacity:.9; }
  h2 { color:var(--title); border-left:5px solid var(--accent); padding-left:9px;
    margin:22px 0 12px; font-size:1.3rem; }
  .league { margin-bottom:34px; }
  .league-title { background:var(--thead-bg); color:var(--thead-fg); font-size:1.5rem;
    font-weight:800; padding:10px 16px; border-radius:8px; margin:8px 0 14px;
    border-left:6px solid var(--accent); letter-spacing:.3px; }
  .cols3 { display:flex; gap:20px; flex-wrap:wrap; margin-bottom:6px; }
  .cols3 > table { flex:1 1 300px; min-width:280px; }
  .il { display:inline-block; background:var(--accent); color:#fff; font-size:.6rem;
    font-weight:700; text-transform:uppercase; letter-spacing:.4px; padding:1px 5px;
    border-radius:4px; margin-left:6px; vertical-align:middle; }
  h3.lg { color:var(--accent); font-size:.8rem; text-transform:uppercase; letter-spacing:.6px;
    margin:14px 0 8px; }
  .cols { display:flex; gap:26px; flex-wrap:wrap; }
  .col { flex:1 1 340px; min-width:300px; }
  table.box { border-collapse:collapse; width:100%; margin:0 0 16px; font-size:.82rem; }
  caption { text-align:left; caption-side:top; font-weight:700; color:var(--title);
    padding:2px 2px 6px; font-size:.82rem; }
  thead th { background:var(--thead-bg); color:var(--thead-fg); font-weight:600; padding:5px 8px;
    text-align:right; font-size:.68rem; text-transform:uppercase; letter-spacing:.3px; white-space:nowrap; }
  thead th.id { text-align:left; }
  tbody td { padding:5px 8px; border-bottom:1px solid var(--line); text-align:right;
    white-space:nowrap; font-variant-numeric:tabular-nums; }
  tbody td.id { text-align:left; font-weight:600; color:var(--title); }
  tbody tr:nth-child(even) { background:var(--row-alt); }
  tr.lead td { background:var(--lead); }
  .clinch { color:var(--accent); font-weight:700; font-size:.72rem; }
  .leaders { display:flex; gap:16px; flex-wrap:wrap; margin-bottom:8px; }
  .leader { flex:1 1 180px; min-width:170px; background:var(--card); border:1px solid var(--line);
    border-radius:8px; padding:10px 12px; }
  .leader h3 { margin:0 0 6px; color:var(--title); font-size:.9rem;
    border-bottom:2px solid var(--accent); padding-bottom:4px; }
  .leader ol { margin:0; padding:0; list-style:none; }
  .leader li { display:flex; align-items:baseline; gap:6px; padding:3px 0; font-size:.8rem;
    border-bottom:1px solid var(--line); }
  .leader li:last-child { border-bottom:0; }
  .lrank { color:var(--muted); width:1.1em; font-size:.72rem; }
  .lname { flex:1; color:var(--fg); }
  .lteam { color:var(--muted); font-size:.72rem; }
  .lval { font-weight:700; color:var(--title); font-variant-numeric:tabular-nums; }
  footer.page { margin-top:24px; padding-top:12px; border-top:1px solid var(--line);
    color:var(--muted); font-size:.78rem; }
  footer .cta { color:var(--title); font-weight:700; }
  .games { display:flex; flex-direction:column; gap:8px; margin-bottom:8px; }
  .game { background:var(--card); border:1px solid var(--line); border-left:4px solid var(--accent);
    border-radius:6px; padding:8px 12px; }
  .game .score { font-weight:600; color:var(--fg); }
  .game .score .win { color:var(--title); }
  .game .gtime { color:var(--muted); font-weight:400; font-size:.8rem; margin-left:8px; }
  .game .meta { color:var(--muted); font-size:.8rem; margin-top:3px; }
  .game .dec { color:var(--accent); font-size:.76rem; margin-top:2px; }
  .game .rhe { font-variant-numeric:tabular-nums; }
  .empty { color:var(--muted); font-style:italic; }
  details.game > summary { cursor:pointer; font-weight:600; color:var(--fg); list-style:none; }
  details.game > summary::-webkit-details-marker { display:none; }
  details.game > summary:before { content:"\\25B8"; color:var(--accent); margin-right:8px; }
  details.game[open] > summary:before { content:"\\25BE"; }
  .boxwrap { margin-top:8px; }
  .bxteam { margin:10px 0 4px; }
  .bxteam h4 { margin:8px 0 4px; color:var(--title); font-size:.92rem;
    border-bottom:2px solid var(--accent); padding-bottom:3px; }
  table.bx { border-collapse:collapse; width:100%; font-size:.76rem; margin-bottom:6px; }
  table.bx thead th { background:var(--thead-bg); color:var(--thead-fg); padding:3px 7px;
    text-align:right; font-size:.66rem; text-transform:uppercase; white-space:nowrap; }
  table.bx thead th.id { text-align:left; }
  table.bx tbody td { padding:3px 7px; border-bottom:1px solid var(--line); text-align:right;
    white-space:nowrap; font-variant-numeric:tabular-nums; }
  table.bx tbody td.id { text-align:left; }
  table.bx td.sub { padding-left:18px; color:var(--fg); }
  table.bx tr.tot td { font-weight:700; border-top:2px solid var(--line); }
  .bxnotes { font-size:.72rem; color:var(--muted); margin:4px 0 10px; }
  .bxnote { margin:1px 0; }
  .bxnote b { color:var(--title); }
  .gameinfo { font-size:.72rem; color:var(--muted); margin-top:6px; padding-top:6px;
    border-top:1px dashed var(--line); }
  .gameinfo .gi { margin-right:12px; }
  .gameinfo .gi b { color:var(--fg); }
</style>
<script>
  (function(){
    var t=null; try { t=localStorage.getItem('sacpros-theme'); } catch(e){}
    if(!t) { t=(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches)?'dark':'light'; }
    document.documentElement.setAttribute('data-theme', t);
  })();
</script>
</head>
<body>
  <header class="page">
    <div class="head-row">
      <div>
        <h1>MLB Standings &amp; Stats</h1>
        <p class="byline">Brought to you by Sacramento Real Estate Agent Michael Rehm</p>
      </div>
      <div class="head-meta">
        <button id="themebtn" class="themebtn" onclick="toggleTheme()">Dark mode</button>
        <div class="updated-top">Updated {{UPDATED}}</div>
      </div>
    </div>
  </header>
  {{BODY}}
  <footer class="page">
    <div class="cta">Brought to you by Sacramento Real Estate Agent {{NAME}}</div>
    {{NAME}} · {{DRE}} · {{PHONE}}. Standings &amp; leaders via the official MLB Stats API; updated daily.
  </footer>
  <script>
    function toggleTheme(){
      var el=document.documentElement;
      var cur=el.getAttribute('data-theme')==='dark'?'light':'dark';
      el.setAttribute('data-theme',cur);
      try { localStorage.setItem('sacpros-theme',cur); } catch(e){}
      var b=document.getElementById('themebtn');
      if(b) b.textContent = cur==='dark' ? 'Light mode' : 'Dark mode';
      if(typeof _postH==='function') _postH();
    }
    function _postH(){
      var h=Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
      try { parent.postMessage({ sacProsHeight: h }, "*"); } catch(e){}
    }
    window.addEventListener('load', function(){
      var b=document.getElementById('themebtn');
      if(b) b.textContent = document.documentElement.getAttribute('data-theme')==='dark' ? 'Light mode' : 'Dark mode';
      _postH();
    });
    window.addEventListener('resize', _postH);
    setTimeout(_postH, 400);
  </script>
</body>
</html>
"""


def main():
    print(f"Building MLB league pages for {YEAR}...")
    standings = fetch_standings(YEAR)
    if not standings:
        standings = fetch_standings(YEAR - 1)

    def league_leaders(yr):
        out = {}
        for code, name, divs, lgid in LEAGUES:
            out[code] = {
                "hit": {k: fetch_leaders(k, "hitting", yr, lgid) for k, _ in HIT_CATS},
                "pit": {k: fetch_leaders(k, "pitching", yr, lgid) for k, _ in PIT_CATS},
            }
        return out

    leaders = league_leaders(YEAR)
    any_hit = any(v for c in leaders.values() for v in c["hit"].values())
    if not any_hit:  # offseason fallback
        leaders = league_leaders(YEAR - 1)

    now = datetime.datetime.now(PT) if PT else datetime.datetime.now(datetime.timezone.utc)
    d_today = now.strftime("%Y-%m-%d")
    d_yest = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    d_tom = (now + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    today = fetch_schedule(d_today)
    yesterday = fetch_schedule(d_yest)
    tomorrow = fetch_schedule(d_tom)
    for g in yesterday:
        if g.get("pk") and g.get("state") == "Final":
            g["box"] = fetch_boxscore(g["pk"])

    tl = team_leagues(standings)
    yb = split_by_league(yesterday, tl)
    tb = split_by_league(today, tl)
    mb = split_by_league(tomorrow, tl)
    games = {c: {"yesterday": yb[c], "today": tb[c], "tomorrow": mb[c]}
             for c in ("AL", "NL")}

    updated = datetime.datetime.now(datetime.timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
    os.makedirs("public", exist_ok=True)
    with open("public/mlb.html", "w", encoding="utf-8") as f:
        f.write(render(standings, leaders, games, updated, YEAR))
    print(f"Built public/mlb.html "
          f"(AL {len(yb['AL'])}/{len(tb['AL'])}/{len(mb['AL'])}, "
          f"NL {len(yb['NL'])}/{len(tb['NL'])}/{len(mb['NL'])} y/t/tom)")


if __name__ == "__main__":
    main()
