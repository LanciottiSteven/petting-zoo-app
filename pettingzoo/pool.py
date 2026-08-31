"""Builds the canonical player pool by merging ESPN + Sleeper + FFC + nflverse."""
from __future__ import annotations
import re, unicodedata
from dataclasses import dataclass, field, asdict

from . import sources as S
from .scoring import score_espn_stats, score_named_stats
from .league import SEASON

ESPN_POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}
ESPN_TEAM = {
    0: "FA", 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN",
    8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LAR",
    15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ", 21: "PHI",
    22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA", 27: "TB", 28: "WSH",
    29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU",
}
# ESPN reports this as ADP for anyone effectively undrafted -> must not be trusted.
ESPN_ADP_FLOOR = 165.0

OUT_STATUSES = {"OUT", "INJURY_RESERVE", "SUSPENSION", "IR", "PUP", "NFI", "DNR", "Sus"}
RISK_STATUSES = {"QUESTIONABLE", "DOUBTFUL", "Questionable", "Doubtful"}


def norm_name(n: str) -> str:
    n = unicodedata.normalize("NFKD", n or "").encode("ascii", "ignore").decode()
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", n.lower())
    return re.sub(r"[^a-z]", "", n)


# ESPN kicker stat ids, confirmed by reproducing ESPN's own applied totals:
#   74/75/76 FG 50+  made/att/missed      77/78/79 FG 40-49
#   80/81/82 FG 0-39                      83/84/85 FG total
#   86/87/88 PAT     made/att/missed
K_STATS = {"80": 3.0, "77": 4.0, "74": 5.0, "86": 1.0, "85": -1.0}


def score_pos(pos: str, stat_entry: dict) -> float:
    """Score a season stat entry with the right engine for the position."""
    stats = stat_entry.get("stats") or {}
    if pos == "K":
        # This league pays 6 for a 60+ FG; ESPN only exposes a combined 50+
        # bucket in projections, so 50+ is valued at 5. Worth well under a
        # point per season and it never moves a draft decision.
        return sum(K_STATS[k] * v for k, v in stats.items() if k in K_STATS)
    if pos == "D/ST":
        # D/ST component ids are not documented and this league layers a
        # yards-allowed tier on top of points-allowed. ESPN's own applied
        # total is the honest estimate for a round-11+ streaming position.
        return float(stat_entry.get("appliedTotal") or 0.0)
    return score_espn_stats(stats)


@dataclass
class Player:
    espn_id: int
    name: str
    pos: str
    team: str
    bye: int | None = None

    # ESPN's own published pre-draft rank — the order everyone else in the
    # league sees on their board. Kept separate from our VOR ranking so both
    # views are available: theirs for "who is on the clock next", ours for
    # "who is actually worth the pick".
    espn_rank: int | None = None
    espn_rank_pos: int | None = None

    proj_espn: float | None = None
    proj_sleeper: float | None = None
    proj: float = 0.0            # blended
    proj_spread: float = 0.0     # |espn - sleeper|, an uncertainty signal

    actual_2025: float | None = None
    games_2025: int | None = None

    adp_espn: float | None = None
    adp_ffc: float | None = None
    adp_stdev: float | None = None
    adp: float = 999.0           # best available consensus
    auction: float | None = None
    pct_owned: float | None = None

    injury_status: str | None = None
    sleeper_status: str | None = None
    depth_chart_order: int | None = None
    games_missed: int = 0        # manual/known suspension or injury games
    flag: str | None = None      # human-readable warning
    note: str | None = None

    week_sd: float = 0.0         # per-game standard deviation of fantasy pts

    def to_dict(self):
        return asdict(self)


def _season_stat(pl: dict, source_id: int, season: int):
    for s in pl.get("stats", []):
        if (s.get("statSourceId") == source_id and s.get("seasonId") == season
                and s.get("statSplitTypeId") == 0 and s.get("stats")):
            return s
    return None


def build_pool(force: bool = False) -> list[Player]:
    espn = S.fetch_espn(force=force)
    schedule = S.fetch_schedule(force=force)
    byes = S.bye_weeks(schedule, SEASON)

    players: list[Player] = []
    for entry in espn.get("players", []):
        pl = entry.get("player") or {}
        pos = ESPN_POS.get(pl.get("defaultPositionId"))
        if not pos:
            continue
        team = ESPN_TEAM.get(pl.get("proTeamId"), "FA")
        own = pl.get("ownership") or {}
        adp_espn = own.get("averageDraftPosition")
        if adp_espn and adp_espn >= ESPN_ADP_FLOOR:
            adp_espn = None                      # undrafted sentinel, not real ADP

        p = Player(
            espn_id=pl.get("id"),
            name=pl.get("fullName", "").strip(),
            pos=pos,
            team=team,
            bye=byes.get(team),
            adp_espn=adp_espn,
            auction=own.get("auctionValueAverage"),
            pct_owned=own.get("percentOwned"),
            injury_status=pl.get("injuryStatus"),
        )
        proj = _season_stat(pl, 1, SEASON)
        if proj:
            p.proj_espn = round(score_pos(pos, proj), 2)
        act = _season_stat(pl, 0, SEASON - 1)
        if act:
            p.actual_2025 = round(score_pos(pos, act), 2)
            p.games_2025 = int(act["stats"].get("210", 0) or 0)
        players.append(p)

    _merge_espn_rankings(players)
    _merge_sleeper(players, force=force)
    _merge_ffc(players, force=force)
    _finalize(players)
    return players


RANKINGS_FILE = S.DATA_DIR / "espn_rankings.json"


def _merge_espn_rankings(players: list[Player]) -> None:
    """ESPN's published pre-draft ranking, parsed from the league's own export."""
    import json
    if not RANKINGS_FILE.exists():
        return
    try:
        rows = json.loads(RANKINGS_FILE.read_text())
    except Exception:
        return
    idx = {norm_name(r["name"]): r for r in rows}
    for p in players:
        r = idx.get(norm_name(p.name))
        if r:
            p.espn_rank = r["espn_rank"]
    # positional rank within ESPN's order (RB1, WR2 ... as ESPN shows them)
    for pos in ("QB", "RB", "WR", "TE", "K", "D/ST"):
        ranked = sorted([p for p in players if p.pos == pos and p.espn_rank],
                        key=lambda p: p.espn_rank)
        for i, p in enumerate(ranked, 1):
            p.espn_rank_pos = i


def _merge_sleeper(players: list[Player], force: bool = False) -> None:
    try:
        sp = S.fetch_sleeper_players(force=force)
        ids = S.fetch_playerids(force=force)
    except Exception:
        return

    espn_to_sleeper = {}
    for row in ids:
        e, s = row.get("espn_id"), row.get("sleeper_id")
        if e and s:
            espn_to_sleeper[str(e).strip()] = str(s).strip()

    by_name = {}
    for pid, rec in sp.items():
        if rec.get("full_name"):
            by_name.setdefault(norm_name(rec["full_name"]), pid)

    # season projections (Rotowire) keyed by sleeper id
    proj_by_sid = {}
    try:
        for r in S.fetch_sleeper_projections(force=force):
            st = r.get("stats") or {}
            if r.get("player_id") and any(k in st for k in ("pts_ppr", "rec", "rush_yd", "pass_yd")):
                proj_by_sid[str(r["player_id"])] = st
    except Exception:
        pass

    for p in players:
        sid = espn_to_sleeper.get(str(p.espn_id)) or by_name.get(norm_name(p.name))
        if not sid:
            continue
        rec = sp.get(sid) or {}
        p.sleeper_status = rec.get("status")
        p.depth_chart_order = rec.get("depth_chart_order")
        if rec.get("injury_status") and not p.injury_status:
            p.injury_status = rec["injury_status"]
        st = proj_by_sid.get(sid)
        if st:
            # rescore from components so league rules apply, not Sleeper's PPR total
            p.proj_sleeper = round(score_named_stats(st), 2)


def _merge_ffc(players: list[Player], force: bool = False) -> None:
    try:
        adp = S.fetch_ffc_adp("ppr", 10, force=force)
    except Exception:
        return
    idx = {}
    for row in adp.get("players", []):
        idx[norm_name(row["name"])] = row
    for p in players:
        row = idx.get(norm_name(p.name))
        if not row:
            continue
        p.adp_ffc = row.get("adp")
        p.adp_stdev = row.get("stdev")
        if p.bye is None and row.get("bye"):
            p.bye = row["bye"]


def _finalize(players: list[Player]) -> None:
    for p in players:
        # --- blended projection -------------------------------------------
        vals = [v for v in (p.proj_espn, p.proj_sleeper) if v is not None and v > 0]
        if len(vals) == 2:
            p.proj = round(0.5 * vals[0] + 0.5 * vals[1], 2)
            p.proj_spread = round(abs(vals[0] - vals[1]), 2)
        elif vals:
            p.proj = vals[0]
        else:
            p.proj = 0.0

        # --- consensus ADP ------------------------------------------------
        adps = [a for a in (p.adp_espn, p.adp_ffc) if a]
        p.adp = round(sum(adps) / len(adps), 2) if adps else 999.0

        # --- availability flags -------------------------------------------
        st = (p.injury_status or "").upper()
        sl = (p.sleeper_status or "")
        if st == "SUSPENSION" or sl == "Suspended":
            p.flag = "SUSPENDED"
        elif st in {"INJURY_RESERVE", "IR"} or sl == "Inactive":
            p.flag = "IR"
        elif st == "OUT":
            p.flag = "OUT"
        elif sl in {"PUP", "NFI"}:
            p.flag = sl
        elif st in {"QUESTIONABLE", "DOUBTFUL"}:
            p.flag = st[:1] + st[1:].lower()
    _apply_games_missed(players)


# ---------------------------------------------------------------- overrides
# Known/likely absences that the structured feeds only expose as a binary flag.
# Edit via the UI or here; games_missed directly discounts the projection.
GAMES_MISSED_OVERRIDES: dict[str, tuple[int, str]] = {}


def _apply_games_missed(players: list[Player]) -> None:
    by_norm = {norm_name(p.name): p for p in players}
    for name, (games, note) in GAMES_MISSED_OVERRIDES.items():
        p = by_norm.get(norm_name(name))
        if p:
            p.games_missed = games
            p.note = note
    for p in players:
        if p.games_missed:
            p.proj = round(p.proj * max(0.0, (17 - p.games_missed) / 17), 2)
            p.flag = p.flag or f"OUT {p.games_missed}G"
