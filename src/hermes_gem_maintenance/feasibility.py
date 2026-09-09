"""Confirming a model is still solvable after a change.

Reloading and re-parsing a candidate (model_io.py, publish.py) proves the file is a
valid model. It says nothing about whether the model can still reach a feasible
solution under its own objective and bounds -- an SBML that parses cleanly can still
be infeasible, and that is a distinct failure mode from every structural check in
checks.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import cobra


# ==== results ====


@dataclass(frozen=True)
class FeasibilityResult:
    """The outcome of optimizing a model under its own bounds and objective."""

    status: str
    objective_value: float | None

    @property
    def ok(self) -> bool:
        """True only when the solver reports an optimal solution."""
        return self.status == "optimal"

    def as_dict(self) -> dict[str, Any]:
        """Structured form for JSON output."""
        return {
            "status": self.status,
            "objective_value": self.objective_value,
            "ok": self.ok,
        }


# ==== check ====


def check_feasibility(model: cobra.Model) -> FeasibilityResult:
    """Optimize a model under its current bounds and objective.

    "Fixed medium and objective" means whatever the SBML already specifies -- this
    package never edits bounds or the objective, so the model's own state on load is
    the fixed condition the request is checked against.
    """
    solution = model.optimize()
    return FeasibilityResult(
        status=solution.status, objective_value=solution.objective_value
    )
