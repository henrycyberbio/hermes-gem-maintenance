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
    add_reaction,
    check_candidate,
    diff_snapshots,
    file_digest,
    load_model,
    save_candidate,
    semantic_snapshot,
    verify_digest,
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
        """Read the frozen model, add the reaction, check it, and write a candidate.

        Args:
            output_dir: Destination for the candidate model and checks. Defaults to a
                runs/ subdirectory named after the reaction.
        """
        request = ReactionRequest.from_dict(
            json.loads(REACTION.read_text(encoding="utf-8"))
        )
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        before = verify_digest(BASE_MODEL, manifest["artifact"]["sha256"])

        destination = (
            Path(output_dir)
            if output_dir
            else REPO_ROOT / "runs" / request.reaction_id
        )
        if destination.exists() and any(destination.iterdir()):
            msg = "output directory is not empty"
            raise ModelIntegrityError(msg, path=destination.name)
        destination.mkdir(parents=True, exist_ok=True)

        base = load_model(BASE_MODEL)
        candidate = base.copy()
        add_reaction(candidate, request)
        result = check_candidate(base, candidate, request)

        candidate_path = save_candidate(
            candidate, destination / "candidate.xml", protected=BASE_MODEL
        )

        # Re-check from disk: serialization is where silent normalization surfaces.
        reloaded = load_model(candidate_path)
        roundtrip = diff_snapshots(
            semantic_snapshot(candidate), semantic_snapshot(reloaded)
        )
        result.record(
            "survives SBML roundtrip",
            ok=not roundtrip,
            detail=str(roundtrip or "identical"),
        )
        result.record(
            "input unchanged",
            ok=file_digest(BASE_MODEL) == before,
            detail=before[:16],
        )

        report = {
            "reaction_id": request.reaction_id,
            "input_sha256": before,
            "candidate_sha256": file_digest(candidate_path),
            **result.as_dict(),
        }
        checks_path = destination / "checks.json"
        checks_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

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
