"""Add one reaction to a frozen GEM and check the result.

Minimal end-to-end walkthrough for the first maintenance case. What this script has
to do by hand is what the package will eventually own; keep it explicit here.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cobra
import fire
from cobra.core.gene import GPR
from cobra.io import read_sbml_model, write_sbml_model

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = REPO_ROOT / "examples" / "add-reaction"
BASE_MODEL = EXAMPLE / "model" / "iEC1372_W3110.xml"
MANIFEST = EXAMPLE / "model" / "iEC1372_W3110.source.json"
REACTION = EXAMPLE / "reaction.json"

logger = logging.getLogger(__name__)


# ==== results ====


@dataclass
class CheckResult:
    """Outcome of the deterministic checks over one candidate model."""

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
        return not self.failed


# ==== helpers ====


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_gpr(rule: str) -> object:
    """Logic-only form of a gene rule.

    COBRApy drops redundant parentheses when writing SBML, so `(A and B) and C`
    returns as `A and B and C`. Comparing rule strings reports those rewrites as
    changes. Flatten nested same-operator nodes and sort operands so the comparison
    sees the boolean logic rather than the syntax.
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


def _semantic_snapshot(model: cobra.Model) -> dict[str, Any]:
    """Structured content only -- SBML layout and element order are not semantics."""
    return {
        "reactions": {
            r.id: {
                "stoichiometry": {m.id: c for m, c in r.metabolites.items()},
                "bounds": list(r.bounds),
                "gene_reaction_rule": _canonical_gpr(r.gene_reaction_rule),
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


def _diff_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
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


# ==== core operations ====


def add_reaction(model: cobra.Model, spec: dict[str, Any]) -> cobra.Reaction:
    """Apply a structured reaction definition to a model in memory.

    Raises on a duplicate identifier or an unknown metabolite; a maintenance tool that
    silently overwrites an existing reaction is worse than one that refuses.
    """
    reaction_id = str(spec["reaction_id"])
    if reaction_id in model.reactions:
        msg = f"reaction {reaction_id} already exists in the model"
        raise ValueError(msg)

    missing = [m for m in spec["metabolites"] if m not in model.metabolites]
    if missing:
        msg = f"metabolites absent from the model: {', '.join(sorted(missing))}"
        raise ValueError(msg)

    reaction = cobra.Reaction(
        id=reaction_id,
        name=str(spec.get("name", "")),
        subsystem=str(spec.get("subsystem", "")),
        lower_bound=float(spec["lower_bound"]),
        upper_bound=float(spec["upper_bound"]),
    )
    model.add_reactions([reaction])
    reaction.add_metabolites(
        {
            model.metabolites.get_by_id(mid): coeff
            for mid, coeff in spec["metabolites"].items()
        }
    )
    if rule := spec.get("gene_reaction_rule"):
        reaction.gene_reaction_rule = str(rule)
    return reaction


def check_candidate(
    base: cobra.Model,
    candidate: cobra.Model,
    spec: dict[str, Any],
) -> CheckResult:
    """Verify the candidate matches the request and changed nothing else."""
    result = CheckResult()
    reaction_id = str(spec["reaction_id"])

    if reaction_id not in candidate.reactions:
        result.record("reaction present", ok=False, detail=f"{reaction_id} missing")
        return result
    reaction = candidate.reactions.get_by_id(reaction_id)
    result.record("reaction present", ok=True, detail=reaction_id)

    wanted = {k: float(v) for k, v in spec["metabolites"].items()}
    actual = {m.id: c for m, c in reaction.metabolites.items()}
    result.record(
        "stoichiometry matches request",
        ok=actual == wanted,
        detail=str(actual),
    )

    wanted_bounds = (float(spec["lower_bound"]), float(spec["upper_bound"]))
    result.record(
        "bounds match request",
        ok=reaction.bounds == wanted_bounds,
        detail=str(reaction.bounds),
    )

    if rule := spec.get("gene_reaction_rule"):
        # Compare logic, not text: SBML roundtripping rewrites equivalent groupings.
        result.record(
            "gene rule matches request",
            ok=_canonical_gpr(reaction.gene_reaction_rule) == _canonical_gpr(str(rule)),
            detail=reaction.gene_reaction_rule,
        )

    # Conservation is only meaningful when every participant carries formula and charge.
    incomplete = [
        m.id for m in reaction.metabolites if not m.formula or m.charge is None
    ]
    if incomplete:
        result.record(
            "mass and charge balance",
            ok=None,
            detail=f"missing formula/charge: {', '.join(sorted(incomplete))}",
        )
    else:
        imbalance = reaction.check_mass_balance()
        result.record(
            "mass and charge balance",
            ok=not imbalance,
            detail=str(imbalance or "balanced"),
        )

    diff = _diff_snapshots(_semantic_snapshot(base), _semantic_snapshot(candidate))
    unrelated = {
        "reactions": [
            r for r in diff.get("reactions", {}).get("added", []) if r != reaction_id
        ],
        "removed_reactions": diff.get("reactions", {}).get("removed", []),
        "changed_reactions": diff.get("reactions", {}).get("changed", []),
        "metabolites": diff.get("metabolites", {}),
        "objective": diff.get("objective"),
    }
    has_unrelated = any(v for v in unrelated.values())
    result.record(
        "no unrelated semantic changes",
        ok=not has_unrelated,
        detail=str(unrelated) if has_unrelated else "only the requested reaction added",
    )
    return result


# ==== cli ====


class Walkthrough:
    """Run the first add-reaction maintenance case against the frozen model."""

    def run(self, output_dir: str = "") -> str:
        """Read the frozen model, add the reaction, check it, and write a candidate.

        Args:
            output_dir: Destination for the candidate model and checks. Defaults to a
                runs/ subdirectory named after the reaction.
        """
        spec = json.loads(REACTION.read_text(encoding="utf-8"))
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

        before_hash = _sha256(BASE_MODEL)
        if before_hash != manifest["artifact"]["sha256"]:
            msg = "frozen model hash does not match its manifest"
            raise ValueError(msg)

        default_dir = REPO_ROOT / "runs" / str(spec["reaction_id"])
        destination = Path(output_dir) if output_dir else default_dir
        if destination.exists() and any(destination.iterdir()):
            msg = f"output directory is not empty: {destination}"
            raise ValueError(msg)
        destination.mkdir(parents=True, exist_ok=True)

        base = read_sbml_model(str(BASE_MODEL))
        candidate = base.copy()
        add_reaction(candidate, spec)
        result = check_candidate(base, candidate, spec)

        candidate_path = destination / "candidate.xml"
        write_sbml_model(candidate, str(candidate_path))

        reloaded = read_sbml_model(str(candidate_path))
        roundtrip = _diff_snapshots(
            _semantic_snapshot(candidate), _semantic_snapshot(reloaded)
        )
        result.record(
            "survives SBML roundtrip",
            ok=not roundtrip,
            detail=str(roundtrip or "identical"),
        )

        if _sha256(BASE_MODEL) != before_hash:
            result.record(
                "input unchanged", ok=False, detail="frozen model was modified"
            )
        else:
            result.record("input unchanged", ok=True, detail=before_hash[:16])

        report = {
            "reaction_id": spec["reaction_id"],
            "input_sha256": before_hash,
            "candidate_sha256": _sha256(candidate_path),
            "passed": result.passed,
            "failed": result.failed,
            "unverifiable": result.unverifiable,
            "status": "passed" if result.ok else "failed",
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
    fire.Fire(Walkthrough)


if __name__ == "__main__":
    main()
