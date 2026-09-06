"""Command-line interface. Parses arguments, calls the API, emits JSON.

Business logic belongs in the modules this imports; nothing here decides anything a
Python caller could not decide the same way.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

import fire

from hermes_gem_maintenance.changes import ReactionRequest, add_reaction
from hermes_gem_maintenance.checks import check_candidate
from hermes_gem_maintenance.errors import GemMaintenanceError, ValidationFailedError
from hermes_gem_maintenance.inspect import (
    describe_metabolite,
    describe_reaction,
    resolve_metabolite,
    summarize,
)
from hermes_gem_maintenance.model_io import (
    file_digest,
    load_model,
    save_candidate,
    verify_digest,
)

logger = logging.getLogger(__name__)


def _emit(payload: dict[str, Any]) -> str:
    """Serialize a result. Fire prints the return value, so no print() is needed."""
    return json.dumps(payload, indent=2, sort_keys=True)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


class Cli:
    """Deterministic operations on genome-scale metabolic models."""

    def inspect(self, model: str, reaction: str = "", metabolite: str = "") -> str:
        """Summarize a model, or describe one reaction or metabolite.

        Args:
            model: Path to an SBML model.
            reaction: Reaction identifier to describe instead of summarizing.
            metabolite: Metabolite identifier to describe instead of summarizing.
        """
        loaded = load_model(Path(model))
        if reaction:
            found = describe_reaction(loaded, reaction)
            return _emit(found or {"absent": reaction, "kind": "reaction"})
        if metabolite:
            found = describe_metabolite(loaded, metabolite)
            return _emit(found or {"absent": metabolite, "kind": "metabolite"})
        return _emit(summarize(loaded))

    def resolve(self, model: str, query: str, compartment: str = "") -> str:
        """List metabolites matching a name or identifier, best match first.

        Several candidates are returned as several candidates. Choosing between them
        is the caller's decision, not this command's.

        Args:
            model: Path to an SBML model.
            query: Name or identifier to look for.
            compartment: Restrict matches to one compartment.
        """
        loaded = load_model(Path(model))
        candidates = resolve_metabolite(loaded, query, compartment or None)
        return _emit(
            {
                "query": query,
                "compartment": compartment or None,
                "count": len(candidates),
                "candidates": candidates,
                "unambiguous": len(candidates) == 1,
            }
        )

    def add_reaction(self, model: str, reaction: str, output: str) -> str:
        """Apply a structured reaction definition, writing a new candidate model.

        Args:
            model: Path to the baseline SBML model; never modified.
            reaction: Path to a JSON reaction definition.
            output: Path for the candidate model; must not already exist.
        """
        baseline = Path(model)
        before = file_digest(baseline)
        request = ReactionRequest.from_dict(_read_json(Path(reaction)))

        loaded = load_model(baseline)
        add_reaction(loaded, request)
        written = save_candidate(loaded, Path(output), protected=baseline)

        verify_digest(baseline, before)
        return _emit(
            {
                "reaction_id": request.reaction_id,
                "candidate": written.name,
                "candidate_sha256": file_digest(written),
                "baseline_sha256": before,
            }
        )

    def check(self, model: str, candidate: str, reaction: str) -> str:
        """Verify a candidate matches the request and changed nothing else.

        Args:
            model: Path to the baseline SBML model.
            candidate: Path to the candidate SBML model.
            reaction: Path to the JSON reaction definition that was requested.
        """
        request = ReactionRequest.from_dict(_read_json(Path(reaction)))
        result = check_candidate(
            load_model(Path(model)), load_model(Path(candidate)), request
        )
        return _emit({"reaction_id": request.reaction_id, **result.as_dict()})

    def export(self, model: str, candidate: str, reaction: str, output: str) -> str:
        """Re-check a candidate and write the deliverable only if it passes.

        The re-check is the point: a candidate is validated again, as loaded from
        disk, immediately before delivery.

        Args:
            model: Path to the baseline SBML model.
            candidate: Path to the candidate SBML model.
            reaction: Path to the JSON reaction definition that was requested.
            output: Path for the deliverable model; must not already exist.
        """
        request = ReactionRequest.from_dict(_read_json(Path(reaction)))
        baseline = Path(model)
        before = file_digest(baseline)

        loaded_candidate = load_model(Path(candidate))
        result = check_candidate(load_model(baseline), loaded_candidate, request)
        if not result.ok:
            msg = "candidate failed re-validation; no deliverable written"
            raise ValidationFailedError(msg, **result.as_dict())

        written = save_candidate(loaded_candidate, Path(output), protected=baseline)
        verify_digest(baseline, before)
        return _emit(
            {
                "reaction_id": request.reaction_id,
                "delivered": written.name,
                "delivered_sha256": file_digest(written),
                **result.as_dict(),
            }
        )


def main() -> None:
    """Entry point. Deliberate errors become structured JSON and a non-zero exit."""
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    try:
        fire.Fire(Cli)
    except GemMaintenanceError as error:
        sys.stderr.write(_emit(error.as_dict()) + "\n")
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
