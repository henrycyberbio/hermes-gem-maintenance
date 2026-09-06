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
    def ok(self) -> bool:
        """True when nothing failed. Unverifiable items do not block."""
        return not self.failed

    def as_dict(self) -> dict[str, Any]:
        """Structured form for JSON output."""
        return {**asdict(self), "status": "passed" if self.ok else "failed"}


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


def semantic_snapshot(model: cobra.Model) -> dict[str, Any]:
    """Structured content only -- SBML layout and element order are not semantics."""
    return {
        "reactions": {
            r.id: {
                "stoichiometry": {m.id: c for m, c in r.metabolites.items()},
                "bounds": list(r.bounds),
                "gene_reaction_rule": canonical_gpr(r.gene_reaction_rule),
            }
            for r in model.reactions
        },
        "metabolites": {
            m.id: {
                "formula": m.formula,
                "charge": m.charge,
                "compartment": m.compartment,
            }
            for m in model.metabolites
        },
        "objective": str(model.objective.expression),
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
    if before["objective"] != after["objective"]:
        diff["objective"] = {"before": before["objective"], "after": after["objective"]}
    return diff


# ==== checks ====


def check_candidate(
    base: cobra.Model,
    candidate: cobra.Model,
    request: ReactionRequest,
) -> CheckResult:
    """Verify the candidate matches the request and changed nothing else."""
    result = CheckResult()

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

    if request.gene_reaction_rule:
        stored = reaction.gene_reaction_rule
        result.record(
            "gene rule matches request",
            ok=canonical_gpr(stored) == canonical_gpr(request.gene_reaction_rule),
            detail=stored,
        )

    name, verdict = _balance_verdict(reaction)
    result.record(name, **verdict)

    diff = diff_snapshots(semantic_snapshot(base), semantic_snapshot(candidate))
    unrelated = _unrelated_changes(diff, request.reaction_id)
    result.record(
        "no unrelated semantic changes",
        ok=not unrelated,
        detail=str(unrelated) if unrelated else "only the requested reaction added",
    )
    return result


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


def _unrelated_changes(diff: dict[str, Any], reaction_id: str) -> dict[str, Any]:
    """Everything in the diff that the request did not ask for."""
    reactions = diff.get("reactions", {})
    found = {
        "added_reactions": [r for r in reactions.get("added", []) if r != reaction_id],
        "removed_reactions": reactions.get("removed", []),
        "changed_reactions": reactions.get("changed", []),
        "metabolites": diff.get("metabolites", {}),
        "objective": diff.get("objective"),
    }
    return {key: value for key, value in found.items() if value}
