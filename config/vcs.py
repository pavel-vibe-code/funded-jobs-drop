"""The 12-VC roster.

Two source platforms covered in v0.1.0:
  - Consider (7 VCs): a16z, Sequoia, Greylock, Lightspeed, Bessemer, Kleiner, Balderton
  - Getro (5 VCs): Accel, General Catalyst, Atomico, Index, Insight Partners

Each entry contains the data needed by the corresponding source adapter to
bootstrap and paginate. Consider needs subdomain + board_id; Getro needs
subdomain + network_id. Both are static per-VC and rarely change.
"""
from __future__ import annotations

from typing import Literal, TypedDict


class ConsiderVC(TypedDict):
    name: str
    subdomain: str         # e.g. "jobs.a16z.com"
    board_id: str          # the Consider board slug (e.g. "andreessen-horowitz")


class GetroVC(TypedDict):
    name: str
    subdomain: str         # e.g. "jobs.accel.com"
    network_id: int        # the Getro collection ID


CONSIDER_VCS: list[ConsiderVC] = [
    {"name": "a16z",       "subdomain": "jobs.a16z.com",            "board_id": "andreessen-horowitz"},
    {"name": "Sequoia",    "subdomain": "jobs.sequoiacap.com",      "board_id": "sequoia-capital"},
    {"name": "Greylock",   "subdomain": "jobs.greylock.com",        "board_id": "greylock-partners"},
    {"name": "Lightspeed", "subdomain": "jobs.lsvp.com",            "board_id": "lightspeed"},
    {"name": "Bessemer",   "subdomain": "jobs.bvp.com",             "board_id": "bessemer-ventures"},
    {"name": "Kleiner",    "subdomain": "jobs.kleinerperkins.com",  "board_id": "kleiner-perkins"},
    {"name": "Balderton",  "subdomain": "careers.balderton.com",    "board_id": "balderton-capital"},
]

GETRO_VCS: list[GetroVC] = [
    {"name": "Accel",            "subdomain": "jobs.accel.com",            "network_id": 8672},
    {"name": "GeneralCatalyst",  "subdomain": "jobs.generalcatalyst.com",  "network_id": 222},
    {"name": "Atomico",          "subdomain": "careers.atomico.com",       "network_id": 36986},
    {"name": "Index",            "subdomain": "indexventures.getro.com",   "network_id": 1629},
    {"name": "Insight",          "subdomain": "jobs.insightpartners.com",  "network_id": 246},
]


def all_vc_names() -> list[str]:
    """All 12 VC names, for use in the Tracker DB's vc_source select options."""
    return [v["name"] for v in CONSIDER_VCS] + [v["name"] for v in GETRO_VCS]
