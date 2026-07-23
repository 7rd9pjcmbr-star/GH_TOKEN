"""Order Pipe — constants & taxonomy."""

from __future__ import annotations

from enum import Enum

# ASUNMEE / ASUMEE primary warehouse (shop 714934229)
ASUMEE_WID = "55e5f0e1-ed06-4dad-b35a-406bee25cdea"
ASUMEE_KHO = "ASUMEE"
ASUMEE_SHOP_ID = 714934229

DEFAULT_STAGES: tuple[str, ...] = (
    "seed",
    "deep",
    "enrich",
    "tracking",
    "pancake_id",
    "accept",
    "waiting",
    "close",
)

# Offline-safe default when user does not pass --live
SAFE_STAGES: tuple[str, ...] = (
    "seed",
    "deep",
    "accept",
    "close",
)


class PathId(str, Enum):
    CLEAR = "PATH-CLEAR"
    WAIT = "PATH-WAIT"
    MISSING = "PATH-MISSING"
    ACCEPT = "PATH-ACCEPT"
    MASK = "PATH-MASK-REDACTION"


class StageId(str, Enum):
    SEED = "seed"
    DEEP = "deep"
    ENRICH = "enrich"
    TRACKING = "tracking"
    PANCAKE_ID = "pancake_id"
    ACCEPT = "accept"
    WAITING = "waiting"
    CLOSE = "close"


# hop N → stage (compat)
HOP_TO_STAGE: dict[int, str] = {
    1: "seed",
    2: "deep",
    3: "deep",
    4: "deep",
    5: "deep",
    6: "enrich",
    7: "enrich",
    8: "tracking",
    9: "pancake_id",
    10: "accept",
    11: "accept",
    12: "waiting",
    13: "close",
}
