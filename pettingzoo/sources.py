"""
Data fetchers. Every source here is free and needs no API key.

  ESPN   fantasy API  -> projections, 2025 actuals, ESPN ADP, auction $, injury status
  Sleeper             -> injury/suspension status, depth charts, cross-platform IDs
  FFC                 -> mock-draft ADP with per-player stdev (opponent modelling)
  nflverse            -> game-level 2020-2025 stats (variance), 2026 schedule + Vegas lines
  DynastyProcess      -> player ID crosswalk (100% coverage across platforms)
"""
from __future__ import annotations
import csv, io, json, time, urllib.request, urllib.parse
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
SEASON = 2026


def _get(url: str, headers: dict | None = None, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# How long each source stays usable before it is re-pulled. These are the
# only knobs that decide freshness, so they live in one place.
HOUR = 3600
TTL = {
    "espn_players.json":       3 * HOUR,    # ADP + injury status: the live stuff
    "ffc_adp_ppr_10.json":    12 * HOUR,    # FFC recomputes once a day
    "sleeper_players.json":   12 * HOUR,    # 15MB; Sleeper asks for <=1 pull/day
    "sleeper_projections.json": 24 * HOUR,  # projections move slowly
    "games.csv":           7 * 24 * HOUR,   # schedule is fixed for the season
    "db_playerids.csv":    7 * 24 * HOUR,   # crosswalk barely changes
}
DEFAULT_TTL = 12 * HOUR


def _cache_path(name: str) -> Path:
    return DATA_DIR / name


def is_stale(name: str) -> bool:
    """True if this source is missing or past its TTL."""
    age = cache_age_seconds(name)
    return age is None or age > TTL.get(name, DEFAULT_TTL)


def _use_cache(name: str, force: bool) -> bool:
    return not force and not is_stale(name)


def data_status() -> list[dict]:
    """Per-source freshness, for display in the UI."""
    out = []
    for name, ttl in TTL.items():
        age = cache_age_seconds(name)
        out.append({
            "source": name,
            "age_seconds": None if age is None else int(age),
            "ttl_seconds": ttl,
            "stale": is_stale(name),
            "missing": age is None,
        })
    return out


def cache_age_seconds(name: str) -> float | None:
    p = _cache_path(name)
    return None if not p.exists() else time.time() - p.stat().st_mtime


def _write(name: str, blob: bytes) -> Path:
    p = _cache_path(name)
    p.write_bytes(blob)
    return p


# --------------------------------------------------------------- ESPN
ESPN_URL = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/"
    f"{SEASON}/segments/0/leaguedefaults/3?view=kona_player_info"
)
# slots: 0 QB, 2 RB, 4 WR, 6 TE, 16 D/ST, 17 K
ESPN_FILTER = {
    "players": {
        "filterSlotIds": {"value": [0, 2, 4, 6, 16, 17]},
        "limit": 1500, "offset": 0,
        "sortPercOwned": {"sortPriority": 1, "sortAsc": False, "value": None},
        "filterStatsForTopScoringPeriodIds": {
            "value": 2,
            "additionalValue": [f"00{SEASON}", f"10{SEASON}", f"00{SEASON-1}", f"02{SEASON}"],
        },
    }
}


def fetch_espn(force: bool = False) -> dict:
    name = "espn_players.json"
    if _use_cache(name, force):
        return json.loads(_cache_path(name).read_bytes())
    blob = _get(ESPN_URL, {"x-fantasy-filter": json.dumps(ESPN_FILTER)})
    _write(name, blob)
    return json.loads(blob)


# --------------------------------------------------------------- Sleeper
def fetch_sleeper_players(force: bool = False) -> dict:
    """~15MB. Sleeper asks that this be called at most once per day."""
    name = "sleeper_players.json"
    if _use_cache(name, force):
        return json.loads(_cache_path(name).read_bytes())
    blob = _get("https://api.sleeper.app/v1/players/nfl", timeout=180)
    _write(name, blob)
    return json.loads(blob)


def fetch_sleeper_projections(force: bool = False) -> list:
    """Season-long projections (Rotowire-sourced) with component stats."""
    name = "sleeper_projections.json"
    if _use_cache(name, force):
        return json.loads(_cache_path(name).read_bytes())
    out = []
    for pos in ("QB", "RB", "WR", "TE", "K"):
        q = urllib.parse.urlencode(
            {"season_type": "regular", "position[]": pos, "order_by": "ppr"})
        try:
            out += json.loads(_get(f"https://api.sleeper.com/projections/nfl/{SEASON}?{q}"))
        except Exception:
            pass
        time.sleep(0.4)   # be polite: 90 req/min limit
    _write(name, json.dumps(out).encode())
    return out


# --------------------------------------------------------------- FFC ADP
def fetch_ffc_adp(fmt: str = "ppr", teams: int = 10, force: bool = False) -> dict:
    name = f"ffc_adp_{fmt}_{teams}.json"
    if _use_cache(name, force):
        return json.loads(_cache_path(name).read_bytes())
    url = (f"https://fantasyfootballcalculator.com/api/v1/adp/{fmt}"
           f"?teams={teams}&year={SEASON}&position=all")
    blob = _get(url)
    _write(name, blob)
    return json.loads(blob)


# --------------------------------------------------------------- nflverse
NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"


def fetch_weekly_stats(season: int, force: bool = False) -> list[dict]:
    name = f"stats_player_week_{season}.csv"
    if not force and _cache_path(name).exists():
        return list(csv.DictReader(_cache_path(name).read_text().splitlines()))
    blob = _get(f"{NFLVERSE}/stats_player/stats_player_week_{season}.csv", timeout=180)
    _write(name, blob)
    return list(csv.DictReader(blob.decode().splitlines()))


def fetch_schedule(force: bool = False) -> list[dict]:
    name = "games.csv"
    if _use_cache(name, force):
        return list(csv.DictReader(_cache_path(name).read_text().splitlines()))
    blob = _get(f"{NFLVERSE}/schedules/games.csv", timeout=180)
    _write(name, blob)
    return list(csv.DictReader(blob.decode().splitlines()))


def fetch_playerids(force: bool = False) -> list[dict]:
    name = "db_playerids.csv"
    if _use_cache(name, force):
        return list(csv.DictReader(_cache_path(name).read_text().splitlines()))
    blob = _get("https://raw.githubusercontent.com/dynastyprocess/data/master/"
                "files/db_playerids.csv", timeout=180)
    _write(name, blob)
    return list(csv.DictReader(blob.decode().splitlines()))


def bye_weeks(schedule: list[dict], season: int = SEASON) -> dict[str, int]:
    games = [g for g in schedule if g["season"] == str(season) and g["game_type"] == "REG"]
    teams = {g["home_team"] for g in games} | {g["away_team"] for g in games}
    played = {t: set() for t in teams}
    for g in games:
        played[g["home_team"]].add(int(g["week"]))
        played[g["away_team"]].add(int(g["week"]))
    weeks = set(range(1, 19))
    out = {}
    for t, w in played.items():
        missing = sorted(weeks - w)
        if missing:
            out[t] = missing[0]
    return out


REFRESHERS = {
    "espn": fetch_espn,
    "sleeper_players": fetch_sleeper_players,
    "sleeper_projections": fetch_sleeper_projections,
    "ffc_adp": fetch_ffc_adp,
    "schedule": fetch_schedule,
    "playerids": fetch_playerids,
}
