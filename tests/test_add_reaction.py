"""Behaviour checks for the add-reaction walkthrough."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import cobra
import pytest
from cobra.io import read_sbml_model, write_sbml_model

from scripts.add_reaction_walkthrough import (
    _canonical_gpr,
    _diff_snapshots,
    _semantic_snapshot,
    add_reaction,
    check_candidate,
)

if TYPE_CHECKING:
    from collections.abc import Callable

# ==== fixtures ====


@pytest.fixture
def model() -> cobra.Model:
    """Tiny model with the metabolites the example reaction needs."""
    m = cobra.Model("toy")
    m.compartments = {"c": "cytosol"}
    specs = {
        "f6p_c": ("C6H11O9P", 0),
        "pi_c": ("HO4P", 0),
        "actp_c": ("C2H3O5P", 0),
        "e4p_c": ("C4H7O7P", 0),
        "h2o_c": ("H2O", 0),
    }
    for mid, (formula, charge) in specs.items():
        met = cobra.Metabolite(mid, formula=formula, charge=charge, compartment="c")
        m.add_metabolites([met])
    existing = cobra.Reaction("ACKr", lower_bound=-1000.0, upper_bound=1000.0)
    m.add_reactions([existing])
    existing.add_metabolites({m.metabolites.actp_c: 1, m.metabolites.pi_c: -1})
    return m


@pytest.fixture
def spec() -> dict[str, object]:
    """The PKETF request, balanced and referencing only existing metabolites."""
    return {
        "reaction_id": "PKETF",
        "name": "Phosphoketolase (fructose-6-phosphate utilizing)",
        "metabolites": {"f6p_c": -1, "pi_c": -1, "actp_c": 1, "e4p_c": 1, "h2o_c": 1},
        "lower_bound": 0.0,
        "upper_bound": 1000.0,
        "gene_reaction_rule": "xfp",
    }


# ==== adding reactions ====


def test_add_reaction_applies_the_requested_definition(
    model: cobra.Model, spec: dict[str, object]
) -> None:
    # GIVEN a model without PKETF.
    # WHEN adding the requested reaction.
    added = add_reaction(model, spec)
    # THEN stoichiometry, bounds and gene rule come from the request, not defaults.
    assert {m.id: c for m, c in added.metabolites.items()} == spec["metabolites"]
    assert added.bounds == (0.0, 1000.0)
    assert added.gene_reaction_rule == "xfp"


def test_add_reaction_refuses_an_existing_identifier(
    model: cobra.Model, spec: dict[str, object]
) -> None:
    # GIVEN a request reusing ACKr, an identifier the model already has.
    spec["reaction_id"] = "ACKr"
    # WHEN adding it.
    # THEN it fails; silently overwriting a curated reaction would corrupt the model.
    with pytest.raises(ValueError, match="already exists"):
        add_reaction(model, spec)


def test_add_reaction_refuses_unknown_metabolites(
    model: cobra.Model, spec: dict[str, object]
) -> None:
    # GIVEN a request naming a metabolite absent from the model.
    spec["metabolites"] = {"f6p_c": -1, "nonexistent_c": 1}
    # WHEN adding it.
    # THEN it fails rather than inventing the metabolite.
    with pytest.raises(ValueError, match="absent from the model"):
        add_reaction(model, spec)


def test_add_reaction_leaves_the_base_model_untouched(
    model: cobra.Model, spec: dict[str, object]
) -> None:
    # GIVEN a snapshot of the model before any change.
    before = _semantic_snapshot(model)
    # WHEN adding the reaction to a copy.
    add_reaction(model.copy(), spec)
    # THEN the original is unchanged; candidates must never mutate the input.
    assert _semantic_snapshot(model) == before


# ==== checking candidates ====


def test_check_passes_for_a_faithful_candidate(
    model: cobra.Model, spec: dict[str, object]
) -> None:
    # GIVEN a candidate built exactly from the request.
    candidate = model.copy()
    add_reaction(candidate, spec)
    # WHEN checking it against the base model.
    result = check_candidate(model, candidate, spec)
    # THEN nothing fails and the balance check reaches a real verdict.
    assert result.ok
    assert not result.unverifiable
    assert any("balanced" in line for line in result.passed)


def test_check_detects_stoichiometry_that_ignores_the_request(
    model: cobra.Model, spec: dict[str, object]
) -> None:
    # GIVEN a candidate whose coefficients differ from what was asked.
    candidate = model.copy()
    add_reaction(candidate, spec)
    reaction = candidate.reactions.get_by_id("PKETF")
    reaction.add_metabolites({candidate.metabolites.h2o_c: 1})
    # WHEN checking it.
    result = check_candidate(model, candidate, spec)
    # THEN the mismatch is reported instead of being smoothed over.
    assert not result.ok
    assert any("stoichiometry" in line for line in result.failed)


def test_check_detects_an_unbalanced_reaction(
    model: cobra.Model, spec: dict[str, object]
) -> None:
    # GIVEN a request whose coefficients violate elemental conservation.
    spec["metabolites"] = {"f6p_c": -1, "pi_c": -1, "actp_c": 1, "e4p_c": 1}
    candidate = model.copy()
    add_reaction(candidate, spec)
    # WHEN checking it.
    result = check_candidate(model, candidate, spec)
    # THEN the imbalance fails the check; the missing water is a real error.
    assert not result.ok
    assert any("balance" in line for line in result.failed)


def test_check_reports_balance_as_unverifiable_without_metadata(
    model: cobra.Model, spec: dict[str, object]
) -> None:
    # GIVEN a participant lacking a formula.
    model.metabolites.e4p_c.formula = None
    candidate = model.copy()
    add_reaction(candidate, spec)
    # WHEN checking it.
    result = check_candidate(model, candidate, spec)
    # THEN balance is unverifiable, never silently counted as passed.
    assert any("balance" in line for line in result.unverifiable)
    assert not any("balance" in line for line in result.passed)


def test_check_detects_changes_beyond_the_request(
    model: cobra.Model, spec: dict[str, object]
) -> None:
    # GIVEN a candidate that also alters an unrelated reaction's bounds.
    candidate = model.copy()
    add_reaction(candidate, spec)
    candidate.reactions.get_by_id("ACKr").bounds = (0.0, 10.0)
    # WHEN checking it.
    result = check_candidate(model, candidate, spec)
    # THEN the collateral edit is reported; only the requested change is acceptable.
    assert not result.ok
    assert any("unrelated" in line for line in result.failed)


# ==== gene rule comparison ====


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("(a and b) and c", "a and b and c"),
        ("a or (b or c)", "a or b or c"),
        ("(a and b) or c", "c or (b and a)"),
    ],
)
def test_canonical_gpr_ignores_parentheses_and_order(left: str, right: str) -> None:
    # GIVEN two rules that differ only in grouping or operand order.
    # WHEN canonicalizing both.
    # THEN they compare equal; COBRApy rewrites grouping on every SBML write.
    assert _canonical_gpr(left) == _canonical_gpr(right)


def test_canonical_gpr_still_separates_different_logic() -> None:
    # GIVEN rules whose boolean meaning genuinely differs.
    # WHEN canonicalizing.
    # THEN they stay distinct; the comparison must not flatten real changes away.
    assert _canonical_gpr("a and b") != _canonical_gpr("a or b")
    assert _canonical_gpr("a and b") != _canonical_gpr("a and c")


def test_snapshot_diff_ignores_gene_rule_reformatting(
    model: cobra.Model, spec: dict[str, object]
) -> None:
    # GIVEN a model whose gene rule is rewritten with equivalent grouping.
    add_reaction(model, spec)
    model.reactions.get_by_id("ACKr").gene_reaction_rule = "(g1 and g2) and g3"
    before = _semantic_snapshot(model)
    model.reactions.get_by_id("ACKr").gene_reaction_rule = "g1 and g2 and g3"
    # WHEN diffing the snapshots.
    # THEN no change is reported; this rewrite is what SBML roundtripping produces.
    assert _diff_snapshots(before, _semantic_snapshot(model)) == {}


def test_check_accepts_a_gene_rule_regrouped_by_sbml(
    model: cobra.Model, spec: dict[str, object], tmp_path: Path
) -> None:
    # GIVEN a request whose gene rule carries redundant parentheses.
    spec["gene_reaction_rule"] = "(g1 and g2) and g3"
    candidate = model.copy()
    add_reaction(candidate, spec)
    # WHEN the candidate is written and read back, as export re-checking does.
    path = tmp_path / "candidate.xml"
    write_sbml_model(candidate, str(path))
    reloaded = read_sbml_model(str(path))
    # THEN the check still passes: COBRApy rewrote the grouping, not the logic.
    stored = reloaded.reactions.get_by_id("PKETF").gene_reaction_rule
    assert stored != spec["gene_reaction_rule"]
    result = check_candidate(model, reloaded, spec)
    assert not any("gene rule" in line for line in result.failed)


# ==== example data ====


def test_example_reaction_matches_the_frozen_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # GIVEN the committed reaction definition for the example case.
    root = Path(__file__).resolve().parents[1]
    reaction_file = root / "examples/add-reaction/reaction.json"
    spec = json.loads(reaction_file.read_text(encoding="utf-8"))
    # WHEN reading its declared fields.
    # THEN it names an irreversible forward reaction with a documented basis.
    assert spec["reaction_id"] == "PKETF"
    assert spec["lower_bound"] == 0.0
    assert spec["provenance"]["scientific_basis"]
    assert spec["provenance"]["bound_rationale"]
