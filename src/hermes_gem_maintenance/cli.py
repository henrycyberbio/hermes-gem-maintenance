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

from hermes_gem_maintenance.changes import ReactionRequest
from hermes_gem_maintenance.checks import check_candidate
from hermes_gem_maintenance.errors import (
    GemMaintenanceError,
    RequestViolationError,
)
from hermes_gem_maintenance.inspect import (
    describe_metabolite,
    describe_reaction,
    resolve_metabolite,
    summarize,
    unique_match,
)
from hermes_gem_maintenance.model_io import load_model
from hermes_gem_maintenance.publish import build_candidate, publish_deliverable

logger = logging.getLogger(__name__)


def _emit(payload: dict[str, Any]) -> str:
    """Serialize a result. Fire prints the return value, so no print() is needed."""
    return json.dumps(payload, indent=2, sort_keys=True)


def _read_json(path: Path) -> dict[str, Any]:
    """Parse a JSON input file, reporting syntax errors inside the taxonomy.

    A file the user hand-edited is user input like any other. Letting a
    JSONDecodeError escape prints a Python traceback where the caller expects a
    structured error with a category, which is exactly the contract this package
    publishes.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"cannot read {path.name}: {exc.strerror or 'unreadable'}"
        raise RequestViolationError(msg, path=path.name) from exc
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        where = f"line {exc.lineno} column {exc.colno}"
        msg = f"{path.name} is not valid JSON: {exc.msg} at {where}"
        raise RequestViolationError(
            msg, path=path.name, line=exc.lineno, column=exc.colno
        ) from exc
    if not isinstance(parsed, dict):
        msg = f"{path.name} must contain a JSON object"
        raise RequestViolationError(msg, path=path.name)
    return parsed


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
        settled = unique_match(candidates)
        return _emit(
            {
                "query": query,
                "compartment": compartment or None,
                "count": len(candidates),
                "candidates": candidates,
                "unambiguous": settled is not None,
                "resolved_id": settled["id"] if settled else None,
            }
        )

    def add_reaction(
        self,
        model: str,
        reaction: str,
        output: str,
        source_manifest: str = "",
        expected_sha256: str = "",
    ) -> str:
        """Apply a structured reaction definition, writing a new candidate model.

        Args:
            model: Path to the baseline SBML model; never modified.
            reaction: Path to a JSON reaction definition.
            output: Path for the candidate model; must not already exist.
            source_manifest: Path to the model's source manifest. When given, the
                baseline must match the SHA-256 it records before anything is read.
            expected_sha256: The approved digest, if there is no manifest.
        """
        request = ReactionRequest.from_dict(_read_json(Path(reaction)))
        result = build_candidate(
            Path(model),
            request,
            Path(output),
            manifest=Path(source_manifest) if source_manifest else None,
            expected_sha256=expected_sha256,
        )
        return _emit(result.as_dict())

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

    def export(
        self,
        model: str,
        candidate: str,
        reaction: str,
        output: str,
        source_manifest: str = "",
        expected_sha256: str = "",
        candidate_sha256: str = "",
    ) -> str:
        """Re-check a candidate and write the deliverable only if it passes.

        The re-check is the point: a candidate is validated again, as loaded from
        disk, immediately before delivery. A candidate whose checks could not be
        decided is refused too -- delivering it would present an untested model as a
        verified one. The deliverable is published by atomic rename after the final
        baseline check, so a failed run never leaves a file at the output path.

        Args:
            model: Path to the baseline SBML model.
            candidate: Path to the candidate SBML model.
            reaction: Path to the JSON reaction definition that was requested.
            output: Path for the deliverable model; must not already exist.
            source_manifest: Path to the model's source manifest. When given, the
                baseline must match the SHA-256 it records.
            expected_sha256: The approved digest, if there is no manifest.
            candidate_sha256: The digest `add_reaction` reported for the candidate.
                When given, the candidate must still match it.
        """
        request = ReactionRequest.from_dict(_read_json(Path(reaction)))
        result = publish_deliverable(
            Path(model),
            Path(candidate),
            request,
            Path(output),
            manifest=Path(source_manifest) if source_manifest else None,
            expected_sha256=expected_sha256,
            candidate_sha256=candidate_sha256,
        )
        return _emit(result.as_dict())


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
