"""Setup orchestrator — wires wizard → Notion init → Profile write → optional AI-50 seed.

Two modes:
  execute_fresh(answers)  — create everything from scratch (first-time setup)
  execute_repair()         — validate existing DBs and patch missing columns
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from setup.ai50_seed_loader import enable as ai50_enable
from setup.notion_init import DB_REGISTRY, create_all, validate_or_patch
from setup.wizard import WizardAnswers, to_profile, validate
from state.config import WorkspaceConfig, load_workspace, save_workspace
from state.notion_client import NotionClient
from state.profile import write_initial as profile_write_initial


@dataclass
class SetupResult:
    db_ids: dict[str, str]
    profile_page_id: str
    ai50_seed_result: Optional[dict]
    repaired: list[str]


def execute_fresh(answers: WizardAnswers) -> SetupResult:
    """Fresh setup: create DBs, write Profile, optionally seed AI-50."""
    errors = validate(answers)
    if errors:
        raise ValueError("Wizard validation failed: " + "; ".join(errors))

    if os.environ.get("FD_DRY_RUN") == "1":
        return SetupResult(
            db_ids={f"{name}_db_id": f"dry-run-{name}" for name in DB_REGISTRY},
            profile_page_id="dry-run-profile-page",
            ai50_seed_result=(
                {"added": 14, "reactivated": 0, "skipped_user_owned": 0}
                if answers.ai50_seed_enabled else None
            ),
            repaired=[],
        )

    client = NotionClient(answers.notion_token)

    # 1. Create the 4 DBs
    db_ids = create_all(client, answers.parent_page_id)

    # 2. Persist workspace config to settings.local.json
    config = WorkspaceConfig(
        notion_token=answers.notion_token,
        parent_page_id=answers.parent_page_id,
        **db_ids,
    )
    save_workspace(config)

    # 3. Write the single Profile row
    profile = to_profile(answers)
    profile_page_id = profile_write_initial(profile)

    # 4. Optional AI-50 seed (if user opted in)
    ai50_result = ai50_enable() if answers.ai50_seed_enabled else None

    return SetupResult(
        db_ids=db_ids,
        profile_page_id=profile_page_id,
        ai50_seed_result=ai50_result,
        repaired=[],
    )


def execute_repair() -> SetupResult:
    """Repair mode: validate existing DBs and patch missing columns without recreating."""
    if os.environ.get("FD_DRY_RUN") == "1":
        return SetupResult(
            db_ids={f"{name}_db_id": f"dry-run-{name}" for name in DB_REGISTRY},
            profile_page_id="",
            ai50_seed_result=None,
            repaired=[],
        )

    config = load_workspace()
    client = NotionClient(config.notion_token)

    db_ids = {
        "tracker_db_id":   config.tracker_db_id,
        "profile_db_id":   config.profile_db_id,
        "favorites_db_id": config.favorites_db_id,
        "runs_db_id":      config.runs_db_id,
    }

    patches = validate_or_patch(client, db_ids)
    return SetupResult(
        db_ids=db_ids,
        profile_page_id="",
        ai50_seed_result=None,
        repaired=[f"{name}: added {props}" for name, props in patches.items()],
    )
