"""
League configuration for "The Petting Zoo" (ESPN league 20543034).

Every value here is transcribed from the league settings export dated 2026-08-26.
The scoring map is validated to reproduce ESPN's own applied totals exactly
(459/460 players within 0.01 pts).
"""
from __future__ import annotations
from dataclasses import dataclass, field

LEAGUE_ID = 20543034
LEAGUE_NAME = "The Petting Zoo"
SEASON = 2026
N_TEAMS = 10
MY_TEAM_NAME = "How's the wife?"

DRAFT_TYPE = "snake"
DRAFT_DATE = "2026-08-30T21:15:00-04:00"
SECONDS_PER_PICK = 90

# ---------------------------------------------------------------- roster
# 9 starters + 5 bench = 14. (2 IR slots do not count against the 14.)
STARTERS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "D/ST": 1, "K": 1}
FLEX_ELIGIBLE = ("RB", "WR", "TE")
BENCH_SLOTS = 5
IR_SLOTS = 2
ROSTER_SIZE = 14
ROSTER_MAX = {"QB": 4, "RB": 8, "WR": 8, "TE": 3, "D/ST": 3, "K": 3}

# ---------------------------------------------------------------- season
REG_SEASON_WEEKS = list(range(1, 15))       # 14 head-to-head matchups
PLAYOFF_ROUND1_WEEKS = [15]
CHAMPIONSHIP_WEEKS = [16, 17]               # two-week total
PLAYOFF_TEAMS = 4
PLAYOFF_SEED_TIEBREAK = "points_for"

DIVISIONS = {
    "EAST": ["How's the wife?", "UV Blue Beatdown", "LEGIO X",
             "Breece's Pieces", "The Cahalans"],
    "WEST": ["Cool Trainer Kai", "Hidden Village Brady's Goo",
             "Pallet Town RMPL Four Skin", "The Unrepresented Voter",
             "LM Boblawler"],
}
TEAM_NAMES = DIVISIONS["EAST"] + DIVISIONS["WEST"]

# Numbers printed on the jerseys in the league logo. They run 1-10 with no gaps
# and cover every team exactly once, so they look like a draft order -- they are
# NOT. Confirmed decorative by the league owner, 2026-08-26. Kept only as a
# team-identity reference; never use these as draft slots.
LOGO_NUMBERS = {
    "How's the wife?": 1,
    "Cool Trainer Kai": 2,
    "UV Blue Beatdown": 3,
    "Hidden Village Brady's Goo": 4,
    "LEGIO X": 5,
    "Breece's Pieces": 6,
    "Pallet Town RMPL Four Skin": 7,
    "The Unrepresented Voter": 8,
    "The Cahalans": 9,
    "LM Boblawler": 10,
}
# Confirmed decorative -- leave False. The real draft order is set by hand by
# the league manager and is not published anywhere the app can read.
USE_LOGO_AS_DRAFT_ORDER = False

# Draft order, from the LM's randomiser (SORT(SEQUENCE(10), RANDARRAY(10))).
# These are the managers' first names, which is how Steven refers to them at the
# table; the ESPN team names in DIVISIONS above cannot be mapped onto them
# reliably, so they are deliberately kept separate rather than guessed.
DRAFT_ORDER = {
    1: "conor",
    2: "Alexander",
    3: "Keaon",
    4: "Eric",
    5: "Steven",        # us — How's the wife?
    6: "Nick",
    7: "Justin",
    8: "Peter",
    9: "Dan",
    10: "Nes",
}
MY_SLOT = 5

LOGO_PATH = "web/assets/logo.png"
LOGO_SMALL_PATH = "web/assets/logo-sm.png"

# ---------------------------------------------------------------- scoring
# ESPN stat-id -> points. Verified by regression against ESPN appliedTotal.
ESPN_STAT_POINTS = {
    # passing
    "3": 0.04,    # passing yards          (1 pt / 25 yds)
    "4": 4.0,     # passing TD
    "20": -2.0,   # interception thrown
    "19": 2.0,    # 2pt passing conversion
    # rushing
    "24": 0.1,    # rushing yards          (1 pt / 10 yds)
    "25": 6.0,    # rushing TD
    "26": 2.0,    # 2pt rushing conversion
    # receiving
    "42": 0.1,    # receiving yards
    "43": 6.0,    # receiving TD
    "44": 2.0,    # 2pt receiving conversion
    "53": 1.0,    # receptions             (FULL PPR)
    # misc
    "72": -2.0,   # fumbles lost
    "63": 6.0,    # fumble recovered for TD
    "101": 6.0,   # kickoff return TD
    "102": 6.0,   # punt return TD
}

# Human-readable scoring, used for the K / D/ST engines where we score from
# component stats rather than trusting a vendor total.
KICKER_POINTS = {
    "pat_made": 1.0, "fg_missed": -1.0,
    "fg_0_39": 3.0, "fg_40_49": 4.0, "fg_50_59": 5.0, "fg_60_plus": 6.0,
}

DST_EVENT_POINTS = {
    "sack": 1.0, "interception": 2.0, "fumble_recovered": 2.0,
    "safety": 2.0, "blocked_kick": 2.0, "def_td": 6.0,
    "return_td": 6.0, "two_pt_return": 2.0, "one_pt_safety": 1.0,
}

# (upper_bound_inclusive, points) — first match wins.
DST_POINTS_ALLOWED_TIERS = [
    (0, 5.0), (6, 4.0), (13, 3.0), (17, 1.0), (21, 0.0),
    (27, 0.0), (34, -1.0), (45, -3.0), (10**6, -5.0),
]
DST_YARDS_ALLOWED_TIERS = [
    (99, 5.0), (199, 3.0), (299, 2.0), (349, 0.0), (399, -1.0),
    (449, -3.0), (499, -5.0), (549, -6.0), (10**6, -7.0),
]

# ---------------------------------------------------------------- transactions
FAAB_BUDGET = 100
WAIVER_PERIOD_DAYS = 1
TRADE_DEADLINE = "2026-12-09T13:00:00-05:00"


@dataclass
class Settings:
    """Mutable per-run settings the UI can change."""
    my_draft_slot: int | None = None      # 1..10, set when the LM publishes order
    n_teams: int = N_TEAMS
    scoring_profile: str = "ppr"
    division_winners_get_top_seeds: bool = True   # ESPN default; flagged in UI

    def validate(self) -> None:
        if self.my_draft_slot is not None and not 1 <= self.my_draft_slot <= self.n_teams:
            raise ValueError(f"draft slot must be 1..{self.n_teams}")


def snake_pick_numbers(slot: int, n_teams: int = N_TEAMS,
                       rounds: int = ROSTER_SIZE) -> list[int]:
    """Overall pick numbers for a given snake draft slot (1-indexed)."""
    picks = []
    for rd in range(rounds):
        if rd % 2 == 0:
            picks.append(rd * n_teams + slot)
        else:
            picks.append(rd * n_teams + (n_teams - slot + 1))
    return picks


def slot_for_pick(pick_no: int, n_teams: int = N_TEAMS) -> int:
    """Inverse of snake_pick_numbers: which slot owns this overall pick."""
    rd, idx = divmod(pick_no - 1, n_teams)
    return idx + 1 if rd % 2 == 0 else n_teams - idx
