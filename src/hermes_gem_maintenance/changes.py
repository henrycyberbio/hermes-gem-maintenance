"""Applying a structured reaction definition to a model."""

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
