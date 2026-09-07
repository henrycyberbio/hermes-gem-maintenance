"""Run the first add-reaction maintenance case against the frozen model.

Sequences the package's operations the way an agent would, so the example is
reproducible without an agent. All logic lives in hermes_gem_maintenance.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import fire

from hermes_gem_maintenance import (
    ReactionRequest,
    build_candidate,
    publish_deliverable,
)
from hermes_gem_maintenance.errors import GemMaintenanceError, ModelIntegrityError

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = REPO_ROOT / "examples" / "add-reaction"
BASE_MODEL = EXAMPLE / "model" / "iEC1372_W3110.xml"
MANIFEST = EXAMPLE / "model" / "iEC1372_W3110.source.json"
REACTION = EXAMPLE / "reaction.json"

logger = logging.getLogger(__name__)


class Walkthrough:
    """Run the worked example end to end."""

    def run(self, output_dir: str = "") -> str:
        """Build a candidate from the frozen model, then publish the deliverable.

        Args:
            output_dir: Destination for the candidate, deliverable and checks.
                Defaults to a runs/ subdirectory named after the reaction.
        """
        request = ReactionRequest.from_dict(
            json.loads(REACTION.read_text(encoding="utf-8"))
        )

        destination = (
            Path(output_dir)
            if output_dir
            else REPO_ROOT / "runs" / request.reaction_id
        )
        if destination.exists() and any(destination.iterdir()):
            msg = "output directory is not empty"
            raise ModelIntegrityError(msg, path=destination.name)
        destination.mkdir(parents=True, exist_ok=True)

        built = build_candidate(
            BASE_MODEL,
            request,
            destination / "candidate.xml",
            manifest=MANIFEST,
        )
        published = publish_deliverable(
            BASE_MODEL,
            built.candidate,
            request,
            destination / "deliverable.xml",
            manifest=MANIFEST,
            candidate_sha256=built.candidate_sha256,
        )

        report = {**built.as_dict(), **published.as_dict()}
        checks_path = destination / "checks.json"
        checks_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

        checks = published.checks
        lines = [
            f"status: {checks['status']}",
            f"artifacts: {destination}",
            f"baseline: {published.baseline_verified_against}",
        ]
        lines += [f"  pass  {item}" for item in checks["passed"]]
        lines += [f"  FAIL  {item}" for item in checks["failed"]]
        lines += [f"  n/a   {item}" for item in checks["unverifiable"]]
        return "\n".join(lines)


def main() -> None:
    """Entry point for the add-reaction walkthrough."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        fire.Fire(Walkthrough)
    except GemMaintenanceError as error:
        logger.exception("%s: %s", error.category, error.message)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
