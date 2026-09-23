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
    build_candidate,
    check_candidate,
    load_model,
    parse_changeset,
)
from hermes_gem_maintenance.errors import GemMaintenanceError, ModelIntegrityError
from hermes_gem_maintenance.model_io import write_check_artifacts

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = REPO_ROOT / "examples" / "add-reaction"
BASE_MODEL = EXAMPLE / "model" / "iEC1372_W3110.xml"
MANIFEST = EXAMPLE / "model" / "iEC1372_W3110.source.json"
CHANGESET = EXAMPLE / "changeset.json"

logger = logging.getLogger(__name__)


class Walkthrough:
    """Run the worked example end to end."""

    def run(self, output_dir: str = "") -> str:
        """Read the frozen model, add the reaction, check it, and write a candidate.

        Args:
            output_dir: Destination for the candidate model and checks. Defaults to a
                runs/ subdirectory named after the reaction.
        """
        request = parse_changeset(
            json.loads(CHANGESET.read_text(encoding="utf-8")),
            expected_type="add_reaction",
        )

        destination = (
            Path(output_dir) if output_dir else REPO_ROOT / "runs" / request.reaction_id
        )
        if destination.exists() and any(destination.iterdir()):
            msg = "output directory is not empty"
            raise ModelIntegrityError(msg, path=destination.name)
        destination.mkdir(parents=True, exist_ok=True)

        candidate_path = destination / "candidate.xml"
        build_candidate(BASE_MODEL, request, candidate_path, manifest=MANIFEST)
        base = load_model(BASE_MODEL)
        candidate = load_model(candidate_path)
        result = check_candidate(base, candidate, request)
        report = {"reaction_id": request.reaction_id, **result.as_dict()}
        write_check_artifacts(
            destination,
            result.semantic_diff,
            report,
            protected=BASE_MODEL,
        )

        lines = [f"status: {report['status']}", f"artifacts: {destination}"]
        lines += [f"  pass  {item}" for item in result.passed]
        lines += [f"  FAIL  {item}" for item in result.failed]
        lines += [f"  n/a   {item}" for item in result.unverifiable]
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
