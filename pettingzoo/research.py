"""
Per-player research dossier. Every number carries its source.

Nothing here is scraped prose or model-generated opinion: it is measured from
game logs and market data, so each line can be traced to a named feed. The
sources are:

  ESPN fantasy API   projections, prior-year actuals, ADP, auction value, %rostered
  Sleeper            injury status, depth-chart order, roster trend
  nflverse           2025 game-by-game logs (the consistency numbers)
  FFC                mock-draft ADP and its standard deviation
"""
from __future__ import annotations
import statistics
from functools import lru_cache

from . import sources as S
from .scoring import score_named_stats
from .pool import norm_name

SOURCES = {
    "espn": ("ESPN Fantasy API", "lm-api-reads.fantasy.espn.com"),
    "sleeper": ("Sleeper API", "api.sleeper.app"),
    "nflverse": ("nflverse game logs", "github.com/nflverse/nflverse-data"),
    "ffc": ("Fantasy Football Calculator", "fantasyfootballcalculator.com"),
    "derived": ("Computed by this app", "league scoring applied to the above"),
}


@lru_cache(maxsize=1)
def _game_logs(season: int = 2025) -> dict[str, list[dict]]:
    """2025 weekly lines per normalised player name, scored under our rules."""
    out: dict[str, list[dict]] = {}
    try:
        rows = S.fetch_weekly_stats(season)
    except Exception:
        return out
    for r in rows:
        if r.get("season_type") != "REG":
            continue
        nm = norm_name(r.get("player_display_name") or "")
        if not nm:
            continue
        pts = score_named_stats(r)
        out.setdefault(nm, []).append({
            "week": int(r["week"]), "pts": round(pts, 2),
            "team": r.get("team"), "opp": r.get("opponent_team"),
            "targets": _f(r.get("targets")), "carries": _f(r.get("carries")),
            "rec": _f(r.get("receptions")),
            "rec_yds": _f(r.get("receiving_yards")),
            "rush_yds": _f(r.get("rushing_yards")),
            "pass_yds": _f(r.get("passing_yards")),
            "tds": _f(r.get("rushing_tds")) + _f(r.get("receiving_tds"))
                   + _f(r.get("passing_tds")),
        })
    for v in out.values():
        v.sort(key=lambda g: g["week"])
    return out


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


@lru_cache(maxsize=1)
def _trending() -> dict[str, int]:
    """Sleeper 24h roster adds — a live read on who the market is chasing."""
    import json, urllib.request
    try:
        req = urllib.request.Request(
            "https://api.sleeper.app/v1/players/nfl/trending/add?lookback_hours=24&limit=200",
            headers=S.UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            rows = json.loads(r.read())
        players = S.fetch_sleeper_players()
        out = {}
        for row in rows:
            p = players.get(str(row["player_id"]))
            if p and p.get("full_name"):
                out[norm_name(p["full_name"])] = int(row["count"])
        return out
    except Exception:
        return {}


def consistency(logs: list[dict]) -> dict:
    """Floor, ceiling, boom/bust — what a projection average hides."""
    if not logs:
        return {}
    pts = [g["pts"] for g in logs]
    pts_sorted = sorted(pts)
    n = len(pts)
    return {
        "games": n,
        "ppg": round(statistics.mean(pts), 1),
        "total": round(sum(pts), 1),
        "best": round(max(pts), 1),
        "worst": round(min(pts), 1),
        "floor_p25": round(pts_sorted[max(0, int(0.25 * n) - 0)], 1),
        "ceiling_p90": round(pts_sorted[min(n - 1, int(0.90 * n))], 1),
        "sd": round(statistics.pstdev(pts), 1) if n > 1 else 0.0,
        "boom_games": sum(1 for p in pts if p >= 20),
        "bust_games": sum(1 for p in pts if p < 10),
        "boom_rate": round(100 * sum(1 for p in pts if p >= 20) / n),
        "bust_rate": round(100 * sum(1 for p in pts if p < 10) / n),
    }


def dossier(player, repl_pts: dict | None = None) -> dict:
    """Everything known about one player, each block tagged with its source."""
    logs = _game_logs().get(norm_name(player.name), [])
    con = consistency(logs)
    trend = _trending().get(norm_name(player.name))

    blocks = []

    # -- projection ----------------------------------------------------
    proj_rows = []
    if player.proj_espn is not None:
        proj_rows.append(("ESPN projection", f"{player.proj_espn:.1f} pts", "espn"))
    if player.proj_sleeper is not None:
        proj_rows.append(("Sleeper projection", f"{player.proj_sleeper:.1f} pts", "sleeper"))
    proj_rows.append(("Blended, this league's scoring", f"{player.proj:.1f} pts", "derived"))
    if player.proj_spread:
        note = ("sources disagree sharply" if player.proj_spread > 35
                else "sources broadly agree")
        proj_rows.append(("Source disagreement", f"{player.proj_spread:.1f} pts — {note}", "derived"))
    if player.proj and player.proj > 0:
        proj_rows.append(("Implied per game (14 wk)", f"{player.proj / 14:.1f} pts", "derived"))
    blocks.append(("Expected 2026 production", proj_rows))

    # -- prior year ----------------------------------------------------
    prior = []
    if player.actual_2025 is not None:
        prior.append(("2025 fantasy points", f"{player.actual_2025:.1f} pts", "espn"))
    if player.games_2025:
        prior.append(("2025 games played", str(player.games_2025), "espn"))
        if player.actual_2025:
            prior.append(("2025 points per game",
                          f"{player.actual_2025 / player.games_2025:.1f}", "derived"))
    if con:
        prior += [
            ("Best / worst week", f"{con['best']} / {con['worst']} pts", "nflverse"),
            ("Weekly floor (25th pct)", f"{con['floor_p25']} pts", "nflverse"),
            ("Weekly ceiling (90th pct)", f"{con['ceiling_p90']} pts", "nflverse"),
            ("Week-to-week SD", f"{con['sd']} pts", "nflverse"),
            ("20+ point games", f"{con['boom_games']} of {con['games']} ({con['boom_rate']}%)", "nflverse"),
            ("Under 10 points", f"{con['bust_games']} of {con['games']} ({con['bust_rate']}%)", "nflverse"),
        ]
    if not prior:
        prior.append(("No 2025 data", "rookie, or did not play", "derived"))
    blocks.append(("2025 actual performance", prior))

    # -- market --------------------------------------------------------
    mkt = []
    if player.adp_espn:
        mkt.append(("ESPN average pick", f"{player.adp_espn:.1f}", "espn"))
    if player.adp_ffc:
        sd = f" (SD {player.adp_stdev:.1f})" if player.adp_stdev else ""
        mkt.append(("FFC mock-draft ADP", f"{player.adp_ffc:.1f}{sd}", "ffc"))
    if player.auction:
        mkt.append(("ESPN auction value", f"${player.auction:.0f}", "espn"))
    if player.pct_owned is not None:
        mkt.append(("Rostered in ESPN leagues", f"{player.pct_owned:.0f}%", "espn"))
    if trend:
        mkt.append(("Sleeper adds, last 24h", f"{trend:,}", "sleeper"))
    blocks.append(("Market signal", mkt))

    # -- situation -----------------------------------------------------
    sit = [("Team / position", f"{player.team} {player.pos}", "espn"),
           ("Bye week", str(player.bye or "—"), "nflverse")]
    if player.depth_chart_order:
        sit.append(("Depth chart", f"#{player.depth_chart_order} at his spot", "sleeper"))
    if player.injury_status:
        sit.append(("Injury status", player.injury_status, "espn"))
    if player.games_missed:
        sit.append(("Games ruled out (manual)", f"{player.games_missed}"
                    + (f" — {player.note}" if player.note else ""), "derived"))
    blocks.append(("Situation", sit))

    # -- value ---------------------------------------------------------
    val = [("Positional rank", f"{player.pos}{getattr(player,'pos_rank','?')}", "derived"),
           ("Tier", str(getattr(player, "tier", "—")), "derived"),
           ("Value over replacement", f"{getattr(player,'vor',0):.1f} pts", "derived")]
    if repl_pts and player.pos in repl_pts:
        val.append((f"Replacement {player.pos}", f"{repl_pts[player.pos]:.1f} pts", "derived"))
    val.append(("Modelled weekly SD", f"{getattr(player,'week_sd',0):.1f} pts", "derived"))
    blocks.append(("Value in this league", val))

    return {"name": player.name, "blocks": blocks, "game_log": logs,
            "consistency": con, "sources": SOURCES}


def summary_lines(player, repl_pts: dict | None = None) -> list[str]:
    """A few plain-language sentences, each traceable to the blocks above."""
    logs = _game_logs().get(norm_name(player.name), [])
    con = consistency(logs)
    out = []

    if player.proj:
        pg = player.proj / 14
        out.append(f"Projects for **{player.proj:.0f} points** this season "
                   f"(~{pg:.1f}/week) under your scoring — blended ESPN + Sleeper.")
    if con:
        out.append(f"In 2025 he played **{con['games']} games** at "
                   f"**{con['ppg']} per game**, hitting 20+ in {con['boom_rate']}% "
                   f"of them and under 10 in {con['bust_rate']}%.")
        if con["bust_rate"] >= 45:
            out.append(f"Volatile: nearly half his weeks were duds "
                       f"(SD {con['sd']}), so he swings games either way.")
        elif con["bust_rate"] <= 20 and con["games"] >= 10:
            out.append(f"Steady: only {con['bust_rate']}% of weeks under 10 points, "
                       f"a reliable weekly starter.")
    elif player.actual_2025 is None:
        out.append("No 2025 NFL production to check — a rookie or a player who "
                   "missed the year, so the projection is the only evidence.")

    if player.proj_spread and player.proj_spread > 35:
        hi, lo = max(player.proj_espn or 0, player.proj_sleeper or 0), \
                 min(player.proj_espn or 0, player.proj_sleeper or 0)
        out.append(f"⚠︎ ESPN and Sleeper disagree by **{player.proj_spread:.0f} points** "
                   f"({hi:.0f} vs {lo:.0f}) — treat the projection as uncertain.")
    if player.flag:
        out.append(f"⚠︎ Currently flagged **{player.flag}**"
                   + (f" — {player.note}" if player.note else "") + ".")
    return out
