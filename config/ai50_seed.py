"""AI-50 supplement: the 14 high-profile AI companies our 12 VCs don't cover.

Per the gap analysis: of the 48 Forbes AI 50 companies, 34 are surfaced via
Consider/Getro VC portfolios. The 14 below are either backed by VCs not in
our 12 (Cognition, Crusoe, Runway, SambaNova, Rogo, Listen Labs), or are
in our VCs' portfolios but choose not to list publicly on the job board
(Cohere, HeyGen, Krea, OpenEvidence, SSI, Surge AI, World Labs, Clay).

Enabled per-user via Profile.ai50_seed_enabled. The loader inserts these
into Favorites with source="seed:ai50" and active=True.

NOTE: careers_url and ats_type for each entry need verification before
v0.1.0 release. Marked entries may shift if companies change ATS providers.
"""
from __future__ import annotations

from typing import TypedDict


class AI50SeedEntry(TypedDict):
    name: str
    careers_url: str
    ats_type: str   # must match a supported adapter in evaluation/ats_adapters.py
    ats_slug: str


# TODO(v0.1.0): verify each careers_url and ats_type against live sites
# before public release. Some companies may move ATS over time.
AI50_SEED: list[AI50SeedEntry] = [
    {"name": "Cohere",                 "careers_url": "https://cohere.com/careers",           "ats_type": "greenhouse", "ats_slug": "cohere"},
    {"name": "Cognition",              "careers_url": "https://www.cognition.ai/careers",     "ats_type": "ashby",      "ats_slug": "cognition"},
    {"name": "Crusoe",                 "careers_url": "https://www.crusoe.ai/careers",        "ats_type": "ashby",      "ats_slug": "crusoe"},
    {"name": "HeyGen",                 "careers_url": "https://www.heygen.com/careers",       "ats_type": "ashby",      "ats_slug": "heygen"},
    {"name": "Krea",                   "careers_url": "https://www.krea.ai/careers",          "ats_type": "ashby",      "ats_slug": "krea"},
    {"name": "Listen Labs",            "careers_url": "https://www.listenlabs.ai/careers",    "ats_type": "ashby",      "ats_slug": "listenlabs"},
    {"name": "OpenEvidence",           "careers_url": "https://www.openevidence.com/careers", "ats_type": "ashby",      "ats_slug": "openevidence"},
    {"name": "Rogo",                   "careers_url": "https://www.rogo.ai/careers",          "ats_type": "ashby",      "ats_slug": "rogo"},
    {"name": "Runway",                 "careers_url": "https://runwayml.com/careers",         "ats_type": "greenhouse", "ats_slug": "runway"},
    {"name": "Safe Superintelligence", "careers_url": "https://ssi.inc/careers",              "ats_type": "ashby",      "ats_slug": "ssi"},
    # Corrected v0.1.3: was (lever, "sambanova") — that's not their public slug.
    # SambaNova publishes on Greenhouse under 'sambanovasystems' (verified 2026-05-15).
    {"name": "SambaNova",              "careers_url": "https://sambanova.ai/careers",         "ats_type": "greenhouse", "ats_slug": "sambanovasystems"},
    {"name": "Surge AI",               "careers_url": "https://www.surgehq.ai/careers",       "ats_type": "ashby",      "ats_slug": "surgehq"},
    {"name": "World Labs",             "careers_url": "https://www.worldlabs.ai/careers",     "ats_type": "ashby",      "ats_slug": "worldlabs"},
    {"name": "Clay",                   "careers_url": "https://www.clay.com/careers",         "ats_type": "ashby",      "ats_slug": "clay"},
]
