"""Behaviour checks for MEMOTE-based consistency snapshots and their diff.

The plan (`local/Hermes-GEM_Plan_v0.1.md`, S10.4) treats a lost stoichiometric
consistency, or a newly introduced mass/charge imbalance, blocked reaction,
dead-end, or orphan metabolite, as a hard failure -- but a model that was already
inconsistent before the change must not be blamed on this run. These tests build
small models with the memote package directly, matching how consistency_snapshot()
calls it, rather than mocking it: a mock could drift from the real function
signatures without any test noticing.
"""

from __future__ import annotations

import builtins
from typing import Any

import cobra
import pytest

from hermes_gem_maintenance import (
    ConsistencyReview as PublicConsistencyReview,
)
from hermes_gem_maintenance import (
    review_consistency as public_review_consistency,
)
from hermes_gem_maintenance.consistency_review import (
    ConsistencyReview,
    compare_consistency,
    consistency_snapshot,
    review_consistency,
)
from hermes_gem_maintenance.errors import DependencyMissingError

# ==== fixtures ====


@pytest.fixture
def balanced_model() -> cobra.Model:
    """A model with one internal reaction, fully balanced, no dead ends."""
    m = cobra.Model("toy")
    m.compartments = {"c": "cytosol", "e": "extracellular"}
    specs = {
        "a_c": ("C1", 0, "c"),
        "b_c": ("C1", 0, "c"),
        "a_e": ("C1", 0, "e"),
        "b_e": ("C1", 0, "e"),
    }
    for mid, (formula, charge, compartment) in specs.items():
        m.add_metabolites(
            [
                cobra.Metabolite(
                    mid, formula=formula, charge=charge, compartment=compartment
                )
            ]
        )
    internal = cobra.Reaction("R1", lower_bound=-1000.0, upper_bound=1000.0)
    m.add_reactions([internal])
    internal.add_metabolites({m.metabolites.a_c: -1, m.metabolites.b_c: 1})
    ex_a = cobra.Reaction("EX_a_e", lower_bound=-1000.0, upper_bound=1000.0)
    m.add_reactions([ex_a])
    ex_a.add_metabolites({m.metabolites.a_e: -1})
    ex_b = cobra.Reaction("EX_b_e", lower_bound=-1000.0, upper_bound=1000.0)
    m.add_reactions([ex_b])
    ex_b.add_metabolites({m.metabolites.b_e: -1})
    transport_a = cobra.Reaction("TR_a", lower_bound=-1000.0, upper_bound=1000.0)
    m.add_reactions([transport_a])
    transport_a.add_metabolites({m.metabolites.a_e: -1, m.metabolites.a_c: 1})
    transport_b = cobra.Reaction("TR_b", lower_bound=-1000.0, upper_bound=1000.0)
    m.add_reactions([transport_b])
    transport_b.add_metabolites({m.metabolites.b_e: -1, m.metabolites.b_c: 1})
    return m


# ==== snapshot ====


def test_consistency_snapshot_reports_a_clean_model_as_clean(
    balanced_model: cobra.Model,
) -> None:
    # GIVEN a model with no known consistency problems.
    # WHEN taking a snapshot.
    snapshot = consistency_snapshot(balanced_model)
    # THEN every tracked category is empty or true.
    assert snapshot.stoichiometrically_consistent
    assert not snapshot.mass_unbalanced
    assert not snapshot.charge_unbalanced
    assert not snapshot.dead_end_metabolites
    assert not snapshot.orphan_metabolites


def test_consistency_snapshot_serializes_all_categories(
    balanced_model: cobra.Model,
) -> None:
    # GIVEN a completed snapshot for a clean model.
    snapshot = consistency_snapshot(balanced_model)
    # WHEN converting it to the durable JSON representation.
    payload = snapshot.as_dict()
    # THEN every tracked category has a stable JSON-compatible shape.
    assert payload == {
        "stoichiometrically_consistent": True,
        "mass_unbalanced": [],
        "charge_unbalanced": [],
        "blocked_reactions": [],
        "dead_end_metabolites": [],
        "orphan_metabolites": [],
    }


def test_consistency_snapshot_detects_a_mass_unbalanced_reaction(
    balanced_model: cobra.Model,
) -> None:
    # GIVEN a reaction whose participants no longer conserve mass.
    balanced_model.reactions.get_by_id("R1").add_metabolites(
        {balanced_model.metabolites.a_c: -1}
    )
    # WHEN taking a snapshot.
    snapshot = consistency_snapshot(balanced_model)
    # THEN the imbalance is reported by category, not folded into a single score.
    assert "R1" in snapshot.mass_unbalanced


def test_consistency_snapshot_detects_a_dead_end_metabolite(
    balanced_model: cobra.Model,
) -> None:
    # GIVEN a metabolite only ever produced by an irreversible reaction, never
    # consumed anywhere. (A reversible producer would also count as a consumer in
    # the reverse direction, so is_only_product() would not flag it.)
    dead_end = cobra.Metabolite("c_c", formula="C1", charge=0, compartment="c")
    balanced_model.add_metabolites([dead_end])
    producer = cobra.Reaction("R_PRODUCE_C", lower_bound=0.0, upper_bound=1000.0)
    balanced_model.add_reactions([producer])
    producer.add_metabolites({balanced_model.metabolites.a_c: -1, dead_end: 1})
    # WHEN taking a snapshot.
    snapshot = consistency_snapshot(balanced_model)
    # THEN it shows up as a dead end: nothing consumes it and it has no exchange.
    assert "c_c" in snapshot.dead_end_metabolites


def test_consistency_snapshot_uses_complete_medium_for_blocked_reactions(
    balanced_model: cobra.Model,
) -> None:
    # GIVEN a reaction that only carries flux if boundary reactions are opened
    # beyond the model's own configured bounds. (Regression: find_blocked_reactions
    # was called without open_exchanges=True, so it answered "blocked under this
    # model's own medium" -- a stricter, different question from MEMOTE's own
    # test_blocked_reactions, which defines "universally blocked" as blocked even
    # under complete medium. On the real project baseline this silently reported
    # roughly three times as many reactions as MEMOTE itself would flag.)
    balanced_model.reactions.get_by_id("EX_a_e").bounds = (0.0, 0.0)
    # The isolated metabolite lives in the extracellular compartment "e", matching
    # the existing exchanges: cobra classifies a boundary reaction as an "exchange"
    # (and therefore something open_exchanges=True will re-open) partly by
    # compartment heuristics, and a reaction in "c" was not picked up as one.
    isolated = cobra.Metabolite("iso_e", formula="C1", charge=0, compartment="e")
    balanced_model.add_metabolites([isolated])
    producer = cobra.Reaction("R_ISO", lower_bound=-1000.0, upper_bound=1000.0)
    balanced_model.add_reactions([producer])
    producer.add_metabolites({balanced_model.metabolites.a_e: -1, isolated: 1})
    # A closed exchange for the new metabolite: R_ISO can only carry flux at all
    # (in either direction) if this, too, is opened beyond its own (0, 0) bounds.
    sink = cobra.Reaction("EX_iso_e", lower_bound=0.0, upper_bound=0.0)
    balanced_model.add_reactions([sink])
    sink.add_metabolites({isolated: -1})
    # WHEN taking a snapshot: under the model's own bounds every path through R_ISO
    # is closed, so only find_blocked_reactions(..., open_exchanges=True) reports it
    # as carrying flux.
    snapshot = consistency_snapshot(balanced_model)
    # THEN it is not reported as blocked, proving open_exchanges=True was honoured.
    assert "R_ISO" not in snapshot.blocked_reactions


# ==== regression ====


def test_compare_consistency_reports_no_regression_between_identical_snapshots(
    balanced_model: cobra.Model,
) -> None:
    # GIVEN the same model snapshotted twice.
    before = consistency_snapshot(balanced_model)
    after = consistency_snapshot(balanced_model)
    # WHEN comparing them.
    regression = compare_consistency(before, after)
    # THEN nothing is reported as new, and the overall verdict is ok.
    assert regression.ok
    assert regression.new_mass_unbalanced == ()
    assert not regression.stoichiometric_consistency_lost


def test_review_consistency_returns_both_snapshots_and_the_regression(
    balanced_model: cobra.Model,
) -> None:
    # GIVEN a baseline and unchanged candidate model.
    # WHEN reviewing consistency as one operation.
    review = review_consistency(balanced_model, balanced_model)
    # THEN both completed snapshots and their derived regression are retained.
    assert isinstance(review, ConsistencyReview)
    assert review.before == review.after
    assert review.regression.ok


def test_consistency_review_is_available_from_the_package_api(
    balanced_model: cobra.Model,
) -> None:
    # GIVEN a caller using the package's documented top-level API.
    # WHEN requesting a consistency review.
    review = public_review_consistency(balanced_model, balanced_model)
    # THEN the concrete review type is available without reaching into a submodule.
    assert isinstance(review, PublicConsistencyReview)


def test_compare_consistency_flags_a_newly_introduced_mass_imbalance(
    balanced_model: cobra.Model,
) -> None:
    # GIVEN a baseline snapshot taken before a reaction is broken.
    before = consistency_snapshot(balanced_model)
    balanced_model.reactions.get_by_id("R1").add_metabolites(
        {balanced_model.metabolites.a_c: -1}
    )
    # WHEN snapshotting after the change and comparing.
    after = consistency_snapshot(balanced_model)
    regression = compare_consistency(before, after)
    # THEN the new imbalance fails the gate by name, and the overall verdict is not ok.
    assert "R1" in regression.new_mass_unbalanced
    assert not regression.ok


def test_compare_consistency_does_not_blame_a_pre_existing_imbalance(
    balanced_model: cobra.Model,
) -> None:
    # GIVEN a baseline that already carries a mass-unbalanced reaction before any
    # change this run makes -- the scenario the plan requires not to be penalised as
    # this change's fault (S10.4).
    balanced_model.reactions.get_by_id("R1").add_metabolites(
        {balanced_model.metabolites.a_c: -1}
    )
    before = consistency_snapshot(balanced_model)
    # WHEN nothing else changes and a second snapshot is taken.
    after = consistency_snapshot(balanced_model)
    regression = compare_consistency(before, after)
    # THEN the pre-existing imbalance is not reported as new, and the gate passes.
    assert regression.new_mass_unbalanced == ()
    assert regression.ok


def test_compare_consistency_flags_a_newly_introduced_blocked_reaction(
    balanced_model: cobra.Model,
) -> None:
    # GIVEN a baseline snapshot taken before a reaction is pinned to zero flux.
    before = consistency_snapshot(balanced_model)
    balanced_model.reactions.get_by_id("TR_b").bounds = (0.0, 0.0)
    # WHEN snapshotting after the change and comparing.
    after = consistency_snapshot(balanced_model)
    regression = compare_consistency(before, after)
    # THEN the newly blocked reaction fails the gate.
    assert "TR_b" in regression.new_blocked_reactions
    assert not regression.ok


# ==== missing dependency ====


def test_consistency_snapshot_reports_a_missing_memote_in_the_taxonomy(
    balanced_model: cobra.Model, monkeypatch: pytest.MonkeyPatch
) -> None:
    # GIVEN an environment where memote cannot be imported.
    # (Regression: the import ran unguarded, so a caller without the optional
    # `memote` dependency group got a bare ModuleNotFoundError instead of a category
    # the documented error taxonomy promises.)
    real_import = builtins.__import__

    def blocked_import(name: str, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        if name.startswith("memote"):
            msg = "blocked for test"
            raise ImportError(msg)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    # WHEN taking a snapshot.
    # THEN it fails inside the taxonomy, naming the missing dependency.
    with pytest.raises(DependencyMissingError) as caught:
        consistency_snapshot(balanced_model)
    assert caught.value.as_dict()["category"] == "dependency_missing"
    assert caught.value.as_dict()["missing"] == "memote"
