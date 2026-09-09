"""Behaviour checks for the FBA feasibility check."""

from __future__ import annotations

import cobra
import pytest

from hermes_gem_maintenance.feasibility import check_feasibility

# ==== fixtures ====


@pytest.fixture
def model() -> cobra.Model:
    """A tiny model with one exchange reaction and an objective."""
    m = cobra.Model("toy")
    m.compartments = {"c": "cytosol"}
    m.add_metabolites(
        [cobra.Metabolite("a_c", formula="C1", charge=0, compartment="c")]
    )
    exchange = cobra.Reaction("EX_a", lower_bound=-10.0, upper_bound=1000.0)
    m.add_reactions([exchange])
    exchange.add_metabolites({m.metabolites.a_c: -1})
    m.objective = "EX_a"
    return m


# ==== checks ====


def test_check_feasibility_reports_optimal_for_a_solvable_model(
    model: cobra.Model,
) -> None:
    # GIVEN a model whose objective can be optimized under its own bounds.
    # WHEN checking feasibility.
    result = check_feasibility(model)
    # THEN the solver reaches an optimal solution and ok is true.
    assert result.status == "optimal"
    assert result.ok


def test_check_feasibility_reports_infeasible_when_bounds_force_an_impossible_flux(
    model: cobra.Model,
) -> None:
    # GIVEN a model whose only reaction is forced to carry flux outside its own
    # feasible range: the sole reaction is required to import a_c (lower bound > 0)
    # while also being capped below that requirement (upper bound < lower bound is
    # rejected by COBRApy itself, so this uses two reactions whose combined bounds
    # cannot be satisfied simultaneously).
    other = cobra.Reaction("DM_a", lower_bound=5.0, upper_bound=5.0)
    model.add_reactions([other])
    other.add_metabolites({model.metabolites.a_c: -1})
    model.reactions.get_by_id("EX_a").bounds = (0.0, 0.0)
    # WHEN checking feasibility: a_c must be consumed at rate 5 by DM_a but EX_a,
    # its only source, is fixed to zero flux -- steady state cannot be reached.
    result = check_feasibility(model)
    # THEN the solver cannot reach a solution and ok is false, not silently passed.
    assert not result.ok
    assert result.status != "optimal"
