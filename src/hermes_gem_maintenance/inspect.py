"""Model summaries, object lookup, and metabolite candidate resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from hermes_gem_maintenance.errors import InsufficientInformationError

if TYPE_CHECKING:
    import cobra


# ==== match kinds ====


@dataclass(frozen=True)
class MatchKind:
    """One way a query can match a metabolite, strongest kinds listed first."""

    label: str
    field: str
    exact: bool

    def matches(self, needle: str, value: str) -> bool:
        return value == needle if self.exact else needle in value


MATCH_KINDS: tuple[MatchKind, ...] = (
    MatchKind("exact identifier", "id", exact=True),
    MatchKind("exact name", "name", exact=True),
    MatchKind("identifier substring", "id", exact=False),
    MatchKind("name substring", "name", exact=False),
)

_MATCH_RANK = {kind.label: rank for rank, kind in enumerate(MATCH_KINDS)}
_EXACT_LABELS = frozenset(kind.label for kind in MATCH_KINDS if kind.exact)


# ==== summaries ====


def summarize(model: cobra.Model) -> dict[str, Any]:
    """Counts and identifiers a caller needs before proposing a change."""
    return {
        "model_id": model.id,
        "reactions": len(model.reactions),
        "metabolites": len(model.metabolites),
        "genes": len(model.genes),
        "compartments": dict(model.compartments),
        "objective": str(model.objective.expression),
    }


def describe_reaction(model: cobra.Model, reaction_id: str) -> dict[str, Any] | None:
    """Structured view of one reaction, or None when it is absent."""
    if reaction_id not in model.reactions:
        return None
    reaction = model.reactions.get_by_id(reaction_id)
    return {
        "id": reaction.id,
        "name": reaction.name,
        "stoichiometry": {m.id: c for m, c in reaction.metabolites.items()},
        "bounds": list(reaction.bounds),
        "gene_reaction_rule": reaction.gene_reaction_rule,
        "subsystem": reaction.subsystem,
    }


def describe_metabolite(
    model: cobra.Model, metabolite_id: str
) -> dict[str, Any] | None:
    """Structured view of one metabolite, or None when it is absent."""
    if metabolite_id not in model.metabolites:
        return None
    metabolite = model.metabolites.get_by_id(metabolite_id)
    return {
        "id": metabolite.id,
        "name": metabolite.name,
        "formula": metabolite.formula,
        "charge": metabolite.charge,
        "compartment": metabolite.compartment,
        "reactions": sorted(r.id for r in metabolite.reactions),
    }


# ==== resolution ====


def resolve_metabolite(
    model: cobra.Model,
    query: str,
    compartment: str | None = None,
) -> list[dict[str, Any]]:
    """Candidate metabolites for a name or identifier, best match first.

    Returns every plausible match with the reason it matched. Deliberately does not
    pick a winner: choosing among several candidates is a judgment the caller must
    make or ask about.
    """
    needle = query.strip().lower()
    candidates: list[dict[str, Any]] = []

    for metabolite in model.metabolites:
        if compartment and metabolite.compartment != compartment:
            continue
        match = _classify(needle, metabolite.id, metabolite.name or "")
        if match is None:
            continue
        candidates.append(
            {
                "id": metabolite.id,
                "name": metabolite.name,
                "compartment": metabolite.compartment,
                "formula": metabolite.formula,
                "charge": metabolite.charge,
                "matched_on": match.label,
            }
        )

    candidates.sort(key=lambda item: (_MATCH_RANK[item["matched_on"]], item["id"]))
    return candidates


def _classify(needle: str, identifier: str, name: str) -> MatchKind | None:
    """Strongest match kind between the query and one metabolite, or None."""
    haystack = {"id": identifier.lower(), "name": name.lower()}
    for kind in MATCH_KINDS:
        if kind.matches(needle, haystack[kind.field]):
            return kind
    return None


def require_unique_metabolite(
    model: cobra.Model,
    query: str,
    compartment: str | None = None,
) -> dict[str, Any]:
    """Resolve to exactly one metabolite or raise with the ambiguity spelled out."""
    candidates = resolve_metabolite(model, query, compartment)
    if not candidates:
        msg = f"no metabolite matches {query!r}"
        raise InsufficientInformationError(msg, query=query, compartment=compartment)

    exact = [c for c in candidates if c["matched_on"] in _EXACT_LABELS]
    if len(exact) == 1:
        return exact[0]
    if len(candidates) == 1:
        return candidates[0]

    msg = f"{query!r} matches {len(candidates)} metabolites; specify which"
    raise InsufficientInformationError(
        msg,
        query=query,
        compartment=compartment,
        candidates=[
            {"id": c["id"], "name": c["name"], "compartment": c["compartment"]}
            for c in candidates[:10]
        ],
    )
