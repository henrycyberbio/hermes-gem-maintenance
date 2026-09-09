"""Applying a structured changeset operation to a model.

A changeset carries exactly one operation for now (plan S11): either `add_reaction`
or `delete_reaction`. `parse_changeset` reads the envelope, `apply_changeset`
dispatches to the matching mutation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Any

import cobra
from cobra.core.gene import GPR

from hermes_gem_maintenance.errors import (
    InsufficientInformationError,
    RequestViolationError,
)

REQUIRED_FIELDS = ("reaction_id", "metabolites", "lower_bound", "upper_bound")


# ==== input validation ====


def _finite(value: object, label: str, reaction_id: str) -> float:
    """Coerce to a finite float, or refuse.

    `float("nan")` and `float("inf")` are accepted by float() and then poison a
    solver silently, so they are rejected here rather than downstream. Booleans are
    refused because `True` would otherwise pass as the coefficient 1.0.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        msg = f"{label} must be numeric"
        raise RequestViolationError(msg, reaction_id=reaction_id)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        msg = f"{label} must be numeric"
        raise RequestViolationError(msg, reaction_id=reaction_id) from exc
    if not isfinite(number):
        msg = f"{label} must be finite"
        raise RequestViolationError(msg, reaction_id=reaction_id)
    return number


def _optional_text(value: object, label: str, reaction_id: str) -> str:
    """An optional string field, defaulted to empty and type-checked when present."""
    if value is None:
        return ""
    if not isinstance(value, str):
        msg = f"{label} must be a string"
        raise RequestViolationError(msg, reaction_id=reaction_id)
    return value


def _gene_rule(value: object, reaction_id: str) -> str:
    """A gene rule that COBRApy can parse, or a refusal.

    COBRApy's setter does not raise on a malformed rule: it logs a parse traceback
    and stores an empty rule. Without this check the package writes a candidate whose
    gene association silently vanished, and the mismatch only surfaces later as a
    validation failure -- the wrong category, blaming the candidate for what is a
    syntax error in the request.
    """
    rule = _optional_text(value, "gene_reaction_rule", reaction_id)
    if not rule:
        return ""
    try:
        parsed = GPR.from_string(rule)
    except (SyntaxError, TypeError, ValueError) as exc:
        msg = "gene_reaction_rule is not a parsable boolean expression"
        raise RequestViolationError(msg, reaction_id=reaction_id) from exc
    if not str(parsed).strip():
        msg = "gene_reaction_rule is not a parsable boolean expression"
        raise RequestViolationError(msg, reaction_id=reaction_id)
    return rule


# ==== request model ====


@dataclass(frozen=True)
class ReactionRequest:
    """A reaction change, fully specified. Free text never reaches the writer."""

    reaction_id: str
    metabolites: Mapping[str, float]
    lower_bound: float
    upper_bound: float
    name: str = ""
    subsystem: str = ""
    gene_reaction_rule: str = ""

    @classmethod
    def from_dict(cls, spec: Mapping[str, Any]) -> ReactionRequest:
        """Build from a parsed definition, reporting what is missing or malformed.

        Every type assumption is checked here. This constructor is the package's
        boundary against hand-written JSON, and a boundary that lets an
        AttributeError escape has published a contract it does not keep.
        """
        if not isinstance(spec, Mapping):
            msg = "reaction definition must be a JSON object"
            raise RequestViolationError(msg)

        absent = [field for field in REQUIRED_FIELDS if spec.get(field) is None]
        if absent:
            msg = f"reaction definition is missing: {', '.join(absent)}"
            raise InsufficientInformationError(msg, missing=absent)

        reaction_id = spec["reaction_id"]
        if not isinstance(reaction_id, str) or not reaction_id.strip():
            msg = "reaction_id must be a non-empty string"
            raise RequestViolationError(msg, reaction_id=str(reaction_id))

        stoichiometry = spec["metabolites"]
        if not isinstance(stoichiometry, Mapping):
            msg = "metabolites must be a JSON object mapping identifier to coefficient"
            raise RequestViolationError(msg, reaction_id=reaction_id)
        if not stoichiometry:
            msg = "reaction definition lists no metabolites"
            raise InsufficientInformationError(msg, missing=["metabolites"])

        blank = [k for k in stoichiometry if not isinstance(k, str) or not k.strip()]
        if blank:
            msg = "metabolite identifiers must be non-empty strings"
            raise RequestViolationError(msg, reaction_id=reaction_id)

        coefficients = {
            str(k): _finite(v, "stoichiometric coefficients", reaction_id)
            for k, v in stoichiometry.items()
        }

        if any(value == 0 for value in coefficients.values()):
            zeros = sorted(k for k, v in coefficients.items() if v == 0)
            msg = "stoichiometric coefficients must be non-zero"
            raise RequestViolationError(msg, metabolites=zeros)

        lower = _finite(spec["lower_bound"], "bounds", reaction_id)
        upper = _finite(spec["upper_bound"], "bounds", reaction_id)

        if lower > upper:
            msg = "lower bound exceeds upper bound"
            raise RequestViolationError(msg, bounds=[lower, upper])

        return cls(
            reaction_id=reaction_id,
            metabolites=coefficients,
            lower_bound=lower,
            upper_bound=upper,
            name=_optional_text(spec.get("name"), "name", reaction_id),
            subsystem=_optional_text(spec.get("subsystem"), "subsystem", reaction_id),
            gene_reaction_rule=_gene_rule(
                spec.get("gene_reaction_rule"), reaction_id
            ),
        )


# ==== mutation ====


def add_reaction(model: cobra.Model, request: ReactionRequest) -> cobra.Reaction:
    """Add one reaction to a model in memory.

    Refuses a duplicate identifier or an unknown metabolite. A maintenance tool that
    silently overwrites a curated reaction is worse than one that stops.
    """
    if request.reaction_id in model.reactions:
        msg = f"reaction {request.reaction_id} already exists in the model"
        raise RequestViolationError(msg, reaction_id=request.reaction_id)

    missing = sorted(m for m in request.metabolites if m not in model.metabolites)
    if missing:
        msg = f"metabolites absent from the model: {', '.join(missing)}"
        raise RequestViolationError(
            msg, reaction_id=request.reaction_id, metabolites=missing
        )

    reaction = cobra.Reaction(
        id=request.reaction_id,
        name=request.name,
        subsystem=request.subsystem,
        lower_bound=request.lower_bound,
        upper_bound=request.upper_bound,
    )
    model.add_reactions([reaction])
    reaction.add_metabolites(
        {
            model.metabolites.get_by_id(mid): coefficient
            for mid, coefficient in request.metabolites.items()
        }
    )
    if request.gene_reaction_rule:
        reaction.gene_reaction_rule = request.gene_reaction_rule
    return reaction


# ==== deletion ====


@dataclass(frozen=True)
class DeleteReactionRequest:
    """A reaction removal, identified by ID alone -- nothing else to specify."""

    reaction_id: str

    @classmethod
    def from_dict(cls, spec: Mapping[str, Any]) -> DeleteReactionRequest:
        """Build from a parsed definition, reporting what is missing or malformed."""
        if not isinstance(spec, Mapping):
            msg = "delete_reaction definition must be a JSON object"
            raise RequestViolationError(msg)

        reaction_id = spec.get("reaction_id")
        if reaction_id is None:
            msg = "delete_reaction definition is missing: reaction_id"
            raise InsufficientInformationError(msg, missing=["reaction_id"])
        if not isinstance(reaction_id, str) or not reaction_id.strip():
            msg = "reaction_id must be a non-empty string"
            raise RequestViolationError(msg, reaction_id=str(reaction_id))

        return cls(reaction_id=reaction_id)


def delete_reaction(
    model: cobra.Model, request: DeleteReactionRequest
) -> cobra.Reaction:
    """Remove one reaction from a model in memory.

    Refuses a reaction ID absent from the model. Deleting something that was never
    there is not a no-op the caller can shrug off -- it means the request named the
    wrong model or the wrong identifier, and proceeding silently would hide that.
    """
    if request.reaction_id not in model.reactions:
        msg = f"reaction {request.reaction_id} does not exist in the model"
        raise RequestViolationError(msg, reaction_id=request.reaction_id)

    reaction = model.reactions.get_by_id(request.reaction_id)
    model.remove_reactions([reaction])
    return reaction


# ==== changeset envelope ====

CHANGESET_OPERATION_TYPES = ("add_reaction", "delete_reaction")


def parse_changeset(
    spec: Mapping[str, Any], *, expected_type: str | None = None
) -> ReactionRequest | DeleteReactionRequest:
    """Parse a changeset envelope into its single operation.

    A changeset is `{"operations": [...]}`. The MVP scope is one operation per
    changeset (plan S11.3): the array shape is kept so a caller-facing request never
    needs to change format if that limit is lifted later, but nothing in this
    package acts on more than one operation today, and a changeset with any other
    length is refused rather than silently truncated.

    `expected_type` lets a caller that only makes sense for one operation kind (the
    CLI's `add_reaction` and `delete_reaction` commands) refuse a changeset of the
    wrong kind with a specific message, instead of a generic type error.
    """
    if not isinstance(spec, Mapping):
        msg = "changeset must be a JSON object"
        raise RequestViolationError(msg)

    operations = spec.get("operations")
    if not isinstance(operations, list):
        msg = "changeset must contain an 'operations' array"
        raise RequestViolationError(msg)
    if len(operations) != 1:
        msg = f"changeset must contain exactly one operation, got {len(operations)}"
        raise RequestViolationError(msg, operation_count=len(operations))

    operation = operations[0]
    if not isinstance(operation, Mapping):
        msg = "each operation must be a JSON object"
        raise RequestViolationError(msg)

    op_type = operation.get("type")
    if expected_type is not None and op_type != expected_type:
        msg = f"changeset operation type must be {expected_type!r}, got {op_type!r}"
        raise RequestViolationError(msg, type=op_type)

    if op_type == "add_reaction":
        return ReactionRequest.from_dict(operation)
    if op_type == "delete_reaction":
        return DeleteReactionRequest.from_dict(operation)
    msg = f"operation type must be one of {CHANGESET_OPERATION_TYPES}, got {op_type!r}"
    raise RequestViolationError(msg, type=op_type)


def apply_changeset(
    model: cobra.Model, request: ReactionRequest | DeleteReactionRequest
) -> cobra.Reaction:
    """Apply one changeset operation to a model in memory.

    Dispatches on the request's own type, the same rule `check_candidate` uses. This
    is the one call site a writer should use: reaching for `add_reaction` or
    `delete_reaction` directly still works for code that already knows which one it
    wants, but a caller that only has a parsed changeset should not need an
    `isinstance` check of its own.
    """
    if isinstance(request, DeleteReactionRequest):
        return delete_reaction(model, request)
    return add_reaction(model, request)
