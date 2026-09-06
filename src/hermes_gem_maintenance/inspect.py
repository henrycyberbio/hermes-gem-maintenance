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
    # Formula ranks above substring kinds: an exact formula is stronger evidence of
    # identity than a fragment of a name. It is the only way to reach a metabolite
    # whose name is unusable -- BiGG stores water as "H2O H2O", so no natural name
    # query finds it, and without this a caller must guess an identifier.
    MatchKind("exact formula", "formula", exact=True),
    MatchKind("identifier substring", "id", exact=False),
    MatchKind("name substring", "name", exact=False),
)

_MATCH_RANK = {kind.label: rank for rank, kind in enumerate(MATCH_KINDS)}
_EXACT_LABELS = frozenset(kind.label for kind in MATCH_KINDS if kind.exact)

# How many weak (substring) matches may sit beside an exact hit before the query is
# treated as too vague to have identified anything. A handful of near-misses is
# normal for a short identifier; a crowd means the caller described a class of
# metabolites rather than one.
WEAK_MATCH_CEILING = 12


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
        match = _classify(
            needle, metabolite.id, metabolite.name or "", metabolite.formula or ""
        )
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


def _classify(
    needle: str, identifier: str, name: str, formula: str
) -> MatchKind | None:
    """Strongest match kind between the query and one metabolite, or None."""
    haystack = {
        "id": identifier.lower(),
        "name": name.lower(),
        "formula": formula.lower(),
    }
    for kind in MATCH_KINDS:
        if haystack[kind.field] and kind.matches(needle, haystack[kind.field]):
            return kind
    return None


def unique_match(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The one candidate a caller may act on, or None when the query is ambiguous.

    A single exact match settles the query even when weaker kinds also matched:
    `pi_c` hits exactly one identifier and several substrings, and treating that as
    ambiguous would block every short identifier. Two exact matches is real
    ambiguity -- the same name in two compartments -- and returns None.

    A vague query is not settled by one exact hit buried in a crowd. `phosphate`
    matches one metabolite named exactly "Phosphate" and 165 others; answering `pi_c`
    would hand the caller a confident mapping for a word that plainly did not
    identify one metabolite. Above WEAK_MATCH_CEILING weak matches, the caller is
    told to narrow the query instead.

    This is the only definition of "unambiguous" in the package. Callers that need a
    verdict use it; reimplementing the rule as `len(candidates) == 1` disagrees with
    it and reports a resolvable query as ambiguous.
    """
    if not candidates:
        return None
    exact = [c for c in candidates if c["matched_on"] in _EXACT_LABELS]
    if len(candidates) - len(exact) > WEAK_MATCH_CEILING:
        return None
    if len(exact) == 1:
        return exact[0]
    if len(candidates) == 1:
        return candidates[0]
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

    match = unique_match(candidates)
    if match is not None:
        return match

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
