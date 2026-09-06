"""Applying a structured reaction definition to a model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import cobra

from hermes_gem_maintenance.errors import (
    InsufficientInformationError,
    RequestViolationError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

REQUIRED_FIELDS = ("reaction_id", "metabolites", "lower_bound", "upper_bound")


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
        """Build from a parsed definition, reporting what is missing or malformed."""
        absent = [field for field in REQUIRED_FIELDS if spec.get(field) is None]
        if absent:
            msg = f"reaction definition is missing: {', '.join(absent)}"
            raise InsufficientInformationError(msg, missing=absent)

        stoichiometry = spec["metabolites"]
        if not stoichiometry:
            msg = "reaction definition lists no metabolites"
            raise InsufficientInformationError(msg, missing=["metabolites"])

        try:
            coefficients = {str(k): float(v) for k, v in stoichiometry.items()}
        except (TypeError, ValueError) as exc:
            msg = "stoichiometric coefficients must be numeric"
            raise RequestViolationError(msg, metabolites=dict(stoichiometry)) from exc

        if any(value == 0 for value in coefficients.values()):
            zeros = sorted(k for k, v in coefficients.items() if v == 0)
            msg = "stoichiometric coefficients must be non-zero"
            raise RequestViolationError(msg, metabolites=zeros)

        try:
            lower = float(spec["lower_bound"])
            upper = float(spec["upper_bound"])
        except (TypeError, ValueError) as exc:
            msg = "bounds must be numeric"
            raise RequestViolationError(msg) from exc

        if lower > upper:
            msg = "lower bound exceeds upper bound"
            raise RequestViolationError(msg, bounds=[lower, upper])

        return cls(
            reaction_id=str(spec["reaction_id"]),
            metabolites=coefficients,
            lower_bound=lower,
            upper_bound=upper,
            name=str(spec.get("name", "")),
            subsystem=str(spec.get("subsystem", "")),
            gene_reaction_rule=str(spec.get("gene_reaction_rule", "")),
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
