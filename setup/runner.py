"""Setup orchestrator — wires wizard → Notion init → Profile write → optional AI-50 seed.

Three modes:
  execute_fresh(answers)               — first-time setup: create DBs, write Profile
  execute_fresh(answers, rewipe=True)  — re-setup after the existing parent page
                                         was archived in Notion (loud guard otherwise)
  execute_repair()                     — patch missing schema columns on existing DBs
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from setup.ai50_seed_loader import enable as ai50_enable
from setup.notion_init import DB_REGISTRY, create_all, validate_or_patch
from setup.wizard import WizardAnswers, to_profile, validate
from state.config import WorkspaceConfig, load_workspace, save_workspace
from state.notion_client import AuthError, NotionClient, NotionError, SetupError
from state.profile import write_initial as profile_write_initial


@dataclass
class SetupResult:
    db_ids: dict[str, str]
    profile_page_id: str
    ai50_seed_result: Optional[dict]
    repaired: list[str]


def execute_fresh(answers: WizardAnswers, rewipe: bool = False) -> SetupResult:
    """Fresh setup: create DBs, write Profile, optionally seed AI-50.

    Refuses if a workspace is already configured (env vars or
    ~/.claude/settings.local.json) unless `rewipe=True`. In rewipe mode the
    existing parent page must be archived in Notion first — protects against
    accidentally creating duplicate DBs alongside live ones.
    """
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

    _guard_against_existing_workspace(answers, rewipe)

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


def _guard_against_existing_workspace(answers: WizardAnswers, rewipe: bool) -> None:
    """Refuse to create duplicate DBs alongside a live workspace.

    Two checks:
      1. Is a workspace already configured (env vars or settings.local.json)?
         → without --rewipe: refuse with a precise message.
      2. With --rewipe: verify the existing parent page is archived (or gone)
         in Notion. Re-running before archiving would leave orphaned DBs.
    """
    try:
        existing = load_workspace()
    except AuthError:
        return  # No prior config — fresh setup is safe.

    if not rewipe:
        raise SetupError(
            "Workspace already configured "
            f"(parent_page_id={existing.parent_page_id}).\n"
            "Choose:\n"
            "  /fd-setup --repair    — patch missing schema columns "
            "(preserves data)\n"
            "  /fd-settings          — edit profile fields\n"
            "  /fd-setup --rewipe    — archive the existing 'Funded Drop' "
            "Notion page first, then re-create (you'll lose Tracker history)"
        )

    # Rewipe path: verify the existing parent page is archived/gone before
    # we create a new set of DBs (otherwise we'd duplicate them).
    client = NotionClient(existing.notion_token)
    try:
        page = client.get_page(existing.parent_page_id)
    except NotionError as e:
        # 404 means the page is gone — that's fine, treat as archived.
        if "HTTP 404" in str(e):
            return
        raise

    if not page.get("archived"):
        raise SetupError(
            f"--rewipe requires the existing parent page "
            f"({existing.parent_page_id}) to be archived in Notion first. "
            "Open it in Notion → ··· menu → Move to Trash, then re-run."
        )
