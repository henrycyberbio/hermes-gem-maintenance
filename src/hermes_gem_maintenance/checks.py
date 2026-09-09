"""Deterministic checks over a candidate model.

Every check here returns a verdict the caller can act on. None of them decide
biology; they establish that the candidate says what the request asked for and
nothing else.
"""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from cobra.core.gene import GPR

from hermes_gem_maintenance.changes import DeleteReactionRequest
from hermes_gem_maintenance.feasibility import check_feasibility

if TYPE_CHECKING:
    import cobra

    from hermes_gem_maintenance.changes import ReactionRequest


# ==== results ====


@dataclass
class CheckResult:
    """Outcome of the deterministic checks over one candidate."""

    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    unverifiable: list[str] = field(default_factory=list)

    def record(self, name: str, *, ok: bool | None, detail: str = "") -> None:
        """File one check under passed, failed, or unverifiable (ok=None)."""
        line = f"{name}: {detail}" if detail else name
        if ok is None:
            self.unverifiable.append(line)
        elif ok:
            self.passed.append(line)
        else:
            self.failed.append(line)

    @property
    def status(self) -> str:
        """One of failed, unverifiable, passed -- in that order of precedence.

        Three states, not two. A check that could not run is not a check that
        succeeded: a candidate whose participants lack formula or charge has never
        been tested for mass balance, and reporting that as `passed` would put an
        unvalidated scientific artifact behind a green light.
        """
        if self.failed:
            return "failed"
        if self.unverifiable:
            return "unverifiable"
        return "passed"

    @property
    def ok(self) -> bool:
        """True only when every check ran and passed."""
        return self.status == "passed"

    @property
    def blocked(self) -> bool:
        """True when something definitively failed, as opposed to being undecided."""
        return bool(self.failed)

    @property
    def scope(self) -> str:
        """Which validation layers this result covers.

        `check_candidate` always returns "structural": layer 1 (this package's own
        request/candidate comparison) plus layer 2 (elemental/charge balance and FBA
        feasibility). It never runs MEMOTE. `ExportResult.as_dict()` overrides this
        field to "export" once `publish_deliverable` has additionally compared MEMOTE
        consistency results against the baseline -- so a caller reading a bare
        `CheckResult` payload (from the `check` command) can tell, without consulting
        documentation, that a passing `status` here does not mean `export` on the
        same candidate would also pass.
        """
        return "structural"

    def as_dict(self) -> dict[str, Any]:
        """Structured form for JSON output."""
        return {**asdict(self), "status": self.status, "scope": self.scope}


# ==== gene rule comparison ====


def canonical_gpr(rule: str) -> object:
    """Logic-only form of a gene rule.

    COBRApy drops redundant parentheses when writing SBML, so `(A and B) and C`
    returns as `A and B and C`. Comparing rule strings reports those rewrites as
    modifications -- on a real model, for dozens of untouched reactions. Flatten
    nested same-operator nodes and sort operands so the comparison sees the boolean
    logic rather than the syntax.

    Use this at every site that compares gene rules. Comparing raw strings anywhere
    reintroduces the bug for candidates that have been through a file.
    """
    if not rule:
        return ""

    def walk(node: ast.AST) -> object:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.BoolOp):
            operator = "and" if isinstance(node.op, ast.And) else "or"
            operands: list[object] = []
            for value in node.values:
                child = walk(value)
                if isinstance(child, tuple) and child[0] == operator:
                    operands.extend(child[1])
                else:
                    operands.append(child)
            return (operator, tuple(sorted(operands, key=str)))
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Expr):
            return walk(node.value)
        if isinstance(node, ast.Module):
            body = node.body
            return walk(body[0] if isinstance(body, list) else body)
        return str(node)

    return walk(GPR.from_string(rule))


# ==== semantic comparison ====

# Model content the snapshot deliberately does not compare. Annotations and notes are
# free-form provenance that COBRApy rewrites on a round trip, so diffing them reports
# noise on every untouched model. Naming them here is the contract: an edit to one of
# these is not caught, and "no unrelated semantic changes" claims nothing about them.
EXCLUDED_FROM_SNAPSHOT = ("annotation", "notes", "SBO terms", "gene names")


def semantic_snapshot(model: cobra.Model) -> dict[str, Any]:
    """Structured content only -- SBML layout and element order are not semantics.

    Every field compared here is one the package promises was left alone. Anything
    omitted can be edited without the "no unrelated semantic changes" check noticing,
    so the omissions are deliberate and listed in EXCLUDED_FROM_SNAPSHOT rather than
    left to be discovered.
    """
    return {
        "reactions": {
            r.id: {
                "stoichiometry": {m.id: c for m, c in r.metabolites.items()},
                "bounds": list(r.bounds),
                "gene_reaction_rule": canonical_gpr(r.gene_reaction_rule),
                "name": r.name or "",
                "subsystem": r.subsystem or "",
            }
            for r in model.reactions
        },
        "metabolites": {
            m.id: {
                "formula": m.formula,
                "charge": m.charge,
                "compartment": m.compartment,
                "name": m.name or "",
            }
            for m in model.metabolites
        },
        "objective": str(model.objective.expression),
        "model_id": model.id or "",
        "compartments": dict(model.compartments),
    }


def diff_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Report what actually changed between two snapshots."""
    diff: dict[str, Any] = {}
    for section in ("reactions", "metabolites"):
        old, new = before[section], after[section]
        added = sorted(set(new) - set(old))
        removed = sorted(set(old) - set(new))
        changed = sorted(k for k in set(old) & set(new) if old[k] != new[k])
        if added or removed or changed:
            diff[section] = {"added": added, "removed": removed, "changed": changed}
    for scalar in ("objective", "model_id", "compartments"):
        if before.get(scalar) != after.get(scalar):
            diff[scalar] = {"before": before.get(scalar), "after": after.get(scalar)}
    return diff


# ==== checks ====


def check_candidate(
    base: cobra.Model,
    candidate: cobra.Model,
    request: ReactionRequest | DeleteReactionRequest,
) -> CheckResult:
    """Verify the candidate matches the request and changed nothing else.

    Dispatches on the request's own type: a `DeleteReactionRequest` is checked
    against removal invariants, everything else against addition invariants. Both
    share the feasibility check and the unrelated-change detector.
    """
    if isinstance(request, DeleteReactionRequest):
        return _check_delete(base, candidate, request)
    return _check_add(base, candidate, request)


def _check_add(
    base: cobra.Model,
    candidate: cobra.Model,
    request: ReactionRequest,
) -> CheckResult:
    """Verify the candidate adds exactly the requested reaction and nothing else."""
    result = CheckResult()

    # The operation is "add", so absence from the baseline is part of the contract.
    # Without it a candidate that is a byte-copy of a baseline already containing the
    # reaction passes every check while having added nothing at all -- and `check`
    # and `export` are public entry points that cannot assume `add_reaction` ran
    # first and refused the duplicate.
    if request.reaction_id in base.reactions:
        result.record(
            "reaction absent from baseline",
            ok=False,
            detail=f"{request.reaction_id} already present before the change",
        )
        return result
    result.record("reaction absent from baseline", ok=True, detail=request.reaction_id)

    if request.reaction_id not in candidate.reactions:
        result.record(
            "reaction present", ok=False, detail=f"{request.reaction_id} missing"
        )
        return result
    reaction = candidate.reactions.get_by_id(request.reaction_id)
    result.record("reaction present", ok=True, detail=request.reaction_id)

    actual = {m.id: c for m, c in reaction.metabolites.items()}
    result.record(
        "stoichiometry matches request",
        ok=actual == dict(request.metabolites),
        detail=str(actual),
    )

    wanted_bounds = (request.lower_bound, request.upper_bound)
    result.record(
        "bounds match request",
        ok=reaction.bounds == wanted_bounds,
        detail=str(reaction.bounds),
    )

    # Compared unconditionally, including when the request left the field empty:
    # a candidate that invents a gene rule, name or subsystem the requester never
    # asked for is exactly the silent edit these checks exist to catch.
    result.record(
        "gene rule matches request",
        ok=canonical_gpr(reaction.gene_reaction_rule)
        == canonical_gpr(request.gene_reaction_rule),
        detail=reaction.gene_reaction_rule or "(none)",
    )
    result.record(
        "name matches request",
        ok=(reaction.name or "") == request.name,
        detail=reaction.name or "(none)",
    )
    result.record("subsystem matches request", **_subsystem_verdict(reaction, request))

    name, verdict = _balance_verdict(reaction)
    result.record(name, **verdict)

    feasibility = check_feasibility(candidate)
    result.record(
        "candidate is solvable under its own bounds and objective",
        ok=feasibility.ok,
        detail=f"{feasibility.status}, objective={feasibility.objective_value}",
    )

    diff = diff_snapshots(semantic_snapshot(base), semantic_snapshot(candidate))
    unrelated = _unrelated_changes(diff, expected_added=request.reaction_id)
    added = diff.get("reactions", {}).get("added", [])
    # Stated as one positive invariant rather than two negatives. Given the
    # absence-from-baseline guard above, `added == [id]` cannot fail on its own --
    # any violation also shows up in `unrelated` -- so the clause is redundant by
    # construction and a mutation removing it survives the suite. It stays because
    # the diff shape is the actual contract of an "add" operation, and reading it
    # here is how a future change to the guard gets caught.
    result.record(
        "diff is exactly the requested addition",
        ok=added == [request.reaction_id] and not unrelated,
        detail=str(unrelated) if unrelated else f"added: {added}",
    )
    return result


def _check_delete(
    base: cobra.Model,
    candidate: cobra.Model,
    request: DeleteReactionRequest,
) -> CheckResult:
    """Verify the candidate removes exactly the requested reaction and nothing else.

    Mirrors `_check_add`'s shape with the invariants inverted: the reaction must
    exist in the baseline (there is nothing to remove otherwise) and be gone from
    the candidate. There is no stoichiometry, bounds, gene rule or balance to check
    -- the reaction itself is gone -- but feasibility and the unrelated-change
    detector apply exactly as they do for an addition.
    """
    result = CheckResult()

    if request.reaction_id not in base.reactions:
        result.record(
            "reaction present in baseline",
            ok=False,
            detail=f"{request.reaction_id} was already absent before the change",
        )
        return result
    result.record(
        "reaction present in baseline", ok=True, detail=request.reaction_id
    )

    if request.reaction_id in candidate.reactions:
        result.record(
            "reaction absent from candidate",
            ok=False,
            detail=f"{request.reaction_id} still present",
        )
        return result
    result.record(
        "reaction absent from candidate", ok=True, detail=request.reaction_id
    )

    feasibility = check_feasibility(candidate)
    result.record(
        "candidate is solvable under its own bounds and objective",
        ok=feasibility.ok,
        detail=f"{feasibility.status}, objective={feasibility.objective_value}",
    )

    diff = diff_snapshots(semantic_snapshot(base), semantic_snapshot(candidate))
    unrelated = _unrelated_changes(diff, expected_removed=request.reaction_id)
    removed = diff.get("reactions", {}).get("removed", [])
    result.record(
        "diff is exactly the requested removal",
        ok=removed == [request.reaction_id] and not unrelated,
        detail=str(unrelated) if unrelated else f"removed: {removed}",
    )
    return result


def _subsystem_verdict(
    reaction: cobra.Reaction, request: ReactionRequest
) -> dict[str, Any]:
    """Subsystem survives a round trip only if the model carries subsystems at all.

    This model's SBML has no subsystem annotations, so COBRApy returns "" for every
    reaction including untouched ones. Failing a requested subsystem against that
    would report the writer's format as a violation by the candidate. An empty stored
    value against a requested one is undecidable, not wrong; anything else is a real
    comparison.
    """
    stored = reaction.subsystem or ""
    if stored == request.subsystem:
        return {"ok": True, "detail": stored or "(none)"}
    if not stored and request.subsystem:
        return {
            "ok": None,
            "detail": f"not retained by this model's SBML: {request.subsystem}",
        }
    return {"ok": False, "detail": stored or "(none)"}


def _balance_verdict(reaction: cobra.Reaction) -> tuple[str, dict[str, Any]]:
    """Balance is only meaningful when every participant has formula and charge."""
    incomplete = sorted(
        m.id for m in reaction.metabolites if not m.formula or m.charge is None
    )
    if incomplete:
        return (
            "mass and charge balance",
            {"ok": None, "detail": f"missing formula/charge: {', '.join(incomplete)}"},
        )
    imbalance = reaction.check_mass_balance()
    return (
        "mass and charge balance",
        {"ok": not imbalance, "detail": str(imbalance or "balanced")},
    )


def _unrelated_changes(
    diff: dict[str, Any],
    *,
    expected_added: str = "",
    expected_removed: str = "",
) -> dict[str, Any]:
    """Everything in the diff that the request did not ask for.

    An addition expects exactly one new reaction id and no removals; a deletion
    expects exactly one removed id and no additions. Passing both keywords empty
    would silently accept any addition or removal as "expected", so callers always
    supply exactly one -- the type checker cannot enforce that, but every call site
    in this module does.
    """
    reactions = diff.get("reactions", {})
    found = {
        "added_reactions": [
            r for r in reactions.get("added", []) if r != expected_added
        ],
        "removed_reactions": [
            r for r in reactions.get("removed", []) if r != expected_removed
        ],
        "changed_reactions": reactions.get("changed", []),
        "metabolites": diff.get("metabolites", {}),
        "objective": diff.get("objective"),
        "model_id": diff.get("model_id"),
        "compartments": diff.get("compartments"),
    }
    return {key: value for key, value in found.items() if value}
