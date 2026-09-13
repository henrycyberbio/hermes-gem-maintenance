"""Behaviour checks for deleting a reaction and the changeset envelope.

Mirrors test_add_reaction.py's fixture shape (toy model with the ACKr reaction
already present) so add and delete are exercised against the same baseline.
"""

from __future__ import annotations

import cobra
import pytest

from hermes_gem_maintenance import (
    DeleteReactionRequest,
    ReactionRequest,
    apply_changeset,
    check_candidate,
    delete_reaction,
    parse_changeset,
    semantic_snapshot,
)
from hermes_gem_maintenance.errors import (
    InsufficientInformationError,
    RequestViolationError,
)

# ==== fixtures ====


@pytest.fixture
def model() -> cobra.Model:
    """Tiny model with one existing reaction (ACKr) and its own exchanges.

    Exchanges for actp_c and pi_c give the network an open boundary, so deleting
    ACKr does not universally block every remaining reaction as an artifact of a
    closed toy network -- matching the convention already established in
    tests/test_cli.py's `baseline` fixture.
    """
    m = cobra.Model("toy")
    m.compartments = {"c": "cytosol"}
    specs = {
        "actp_c": ("C2H3O5P", 0),
        "pi_c": ("HO4P", 0),
    }
    for mid, (formula, charge) in specs.items():
        m.add_metabolites(
            [cobra.Metabolite(mid, formula=formula, charge=charge, compartment="c")]
        )
    existing = cobra.Reaction("ACKr", lower_bound=-1000.0, upper_bound=1000.0)
    m.add_reactions([existing])
    existing.add_metabolites({m.metabolites.actp_c: 1, m.metabolites.pi_c: -1})
    ex_actp = cobra.Reaction("EX_actp_c", lower_bound=-1000.0, upper_bound=1000.0)
    m.add_reactions([ex_actp])
    ex_actp.add_metabolites({m.metabolites.actp_c: -1})
    ex_pi = cobra.Reaction("EX_pi_c", lower_bound=-1000.0, upper_bound=1000.0)
    m.add_reactions([ex_pi])
    ex_pi.add_metabolites({m.metabolites.pi_c: -1})
    return m


# ==== parsing a delete request ====


def test_delete_request_rejects_a_definition_missing_reaction_id() -> None:
    # GIVEN a definition with no reaction_id.
    # WHEN parsing it.
    # THEN it reports what is missing, so the caller can ask a specific question.
    with pytest.raises(InsufficientInformationError) as caught:
        DeleteReactionRequest.from_dict({})
    assert caught.value.context["missing"] == ["reaction_id"]


def test_delete_request_rejects_a_non_string_reaction_id() -> None:
    # GIVEN a definition whose reaction_id is the wrong type.
    # WHEN parsing it.
    # THEN it fails as a violation, not an AttributeError further downstream.
    with pytest.raises(RequestViolationError, match="non-empty string"):
        DeleteReactionRequest.from_dict({"reaction_id": 42})


def test_delete_request_rejects_a_non_mapping_definition() -> None:
    # GIVEN a definition that is not a JSON object at all.
    # WHEN parsing it.
    # THEN it fails inside the taxonomy, matching ReactionRequest's own boundary.
    with pytest.raises(RequestViolationError, match="JSON object"):
        DeleteReactionRequest.from_dict(["ACKr"])  # type: ignore[arg-type]


def test_delete_request_accepts_a_bare_reaction_id() -> None:
    # GIVEN the minimal legal definition.
    # WHEN parsing it.
    # THEN it succeeds; nothing but an identifier is needed to name a removal.
    request = DeleteReactionRequest.from_dict({"reaction_id": "ACKr"})
    assert request.reaction_id == "ACKr"


# ==== deleting reactions ====


def test_delete_reaction_removes_it_from_the_model(model: cobra.Model) -> None:
    # GIVEN a model containing ACKr.
    request = DeleteReactionRequest.from_dict({"reaction_id": "ACKr"})
    # WHEN deleting it.
    removed = delete_reaction(model, request)
    # THEN it is gone from the model, and the returned object still describes it.
    assert "ACKr" not in model.reactions
    assert removed.id == "ACKr"


def test_delete_reaction_refuses_an_absent_identifier(model: cobra.Model) -> None:
    # GIVEN a request naming a reaction the model does not have.
    request = DeleteReactionRequest.from_dict({"reaction_id": "NOPE"})
    # WHEN deleting it.
    # THEN it fails rather than silently doing nothing.
    with pytest.raises(RequestViolationError, match="does not exist"):
        delete_reaction(model, request)


def test_delete_reaction_leaves_the_base_model_untouched(model: cobra.Model) -> None:
    # GIVEN a snapshot of the model before any change.
    before = semantic_snapshot(model)
    request = DeleteReactionRequest.from_dict({"reaction_id": "ACKr"})
    # WHEN deleting from a copy.
    delete_reaction(model.copy(), request)
    # THEN the original is unchanged; candidates must never mutate the input.
    assert semantic_snapshot(model) == before


# ==== checking a deletion candidate ====


def test_check_passes_for_a_faithful_deletion(model: cobra.Model) -> None:
    # GIVEN a candidate with ACKr correctly removed.
    request = DeleteReactionRequest.from_dict({"reaction_id": "ACKr"})
    candidate = model.copy()
    delete_reaction(candidate, request)
    # WHEN checking it against the base model.
    result = check_candidate(model, candidate, request)
    # THEN nothing fails.
    assert result.ok


def test_check_rejects_a_candidate_that_deleted_nothing(model: cobra.Model) -> None:
    # GIVEN a candidate that is an unchanged copy of the baseline.
    request = DeleteReactionRequest.from_dict({"reaction_id": "ACKr"})
    candidate = model.copy()
    # WHEN checking it.
    result = check_candidate(model, candidate, request)
    # THEN it fails: the operation is "delete", so presence in the candidate is a
    # contradiction, not a success.
    assert not result.ok
    assert any("absent from candidate" in line for line in result.failed)


def test_check_rejects_deleting_a_reaction_absent_from_the_baseline(
    model: cobra.Model,
) -> None:
    # GIVEN a request naming a reaction that was never in the baseline.
    request = DeleteReactionRequest.from_dict({"reaction_id": "NOPE"})
    # WHEN checking a copy of the model (which also lacks NOPE) against it.
    result = check_candidate(model, model.copy(), request)
    # THEN it fails: there is nothing to have deleted.
    assert not result.ok
    assert any("present in baseline" in line for line in result.failed)


def test_check_rejects_an_extra_removal_alongside_the_requested_one(
    model: cobra.Model,
) -> None:
    # GIVEN a candidate that removes the requested reaction and one extra reaction.
    request = DeleteReactionRequest.from_dict({"reaction_id": "ACKr"})
    candidate = model.copy()
    delete_reaction(candidate, request)
    candidate.remove_reactions([candidate.reactions.get_by_id("EX_pi_c")])
    # WHEN checking it.
    result = check_candidate(model, candidate, request)
    # THEN the extra removal fails the invariant.
    assert not result.ok
    assert any("no unrelated semantic changes" in line for line in result.failed)


# ==== changeset envelope ====


def test_parse_changeset_reads_a_single_add_reaction_operation() -> None:
    # GIVEN a changeset wrapping one add_reaction operation.
    changeset = {
        "operations": [
            {
                "type": "add_reaction",
                "reaction_id": "NEWRXN",
                "metabolites": {"actp_c": -1, "pi_c": 1},
                "lower_bound": 0.0,
                "upper_bound": 1000.0,
            }
        ]
    }
    # WHEN parsing it.
    request = parse_changeset(changeset)
    # THEN it returns the same ReactionRequest a bare definition would produce.
    assert isinstance(request, ReactionRequest)
    assert request.reaction_id == "NEWRXN"


def test_parse_changeset_reads_a_single_delete_reaction_operation() -> None:
    # GIVEN a changeset wrapping one delete_reaction operation.
    changeset = {"operations": [{"type": "delete_reaction", "reaction_id": "ACKr"}]}
    # WHEN parsing it.
    request = parse_changeset(changeset)
    # THEN it returns a DeleteReactionRequest.
    assert isinstance(request, DeleteReactionRequest)
    assert request.reaction_id == "ACKr"


def test_parse_changeset_refuses_more_than_one_operation() -> None:
    # GIVEN a changeset with two operations.
    changeset = {
        "operations": [
            {"type": "delete_reaction", "reaction_id": "ACKr"},
            {"type": "delete_reaction", "reaction_id": "OTHER"},
        ]
    }
    # WHEN parsing it.
    # THEN it is refused, not silently truncated to the first operation.
    with pytest.raises(RequestViolationError, match="exactly one operation"):
        parse_changeset(changeset)


def test_parse_changeset_refuses_zero_operations() -> None:
    # GIVEN a changeset with an empty operations array.
    # WHEN parsing it.
    # THEN it is refused; an empty changeset changes nothing and cannot be applied.
    with pytest.raises(RequestViolationError, match="exactly one operation"):
        parse_changeset({"operations": []})


def test_parse_changeset_refuses_an_unknown_operation_type() -> None:
    # GIVEN an operation whose type this package does not implement.
    changeset = {"operations": [{"type": "rename_reaction", "reaction_id": "ACKr"}]}
    # WHEN parsing it.
    # THEN it is refused, naming the unsupported type.
    with pytest.raises(RequestViolationError, match="rename_reaction"):
        parse_changeset(changeset)


def test_parse_changeset_enforces_the_expected_type_when_given() -> None:
    # GIVEN a changeset containing a delete operation.
    changeset = {"operations": [{"type": "delete_reaction", "reaction_id": "ACKr"}]}
    # WHEN parsing it with expected_type="add_reaction".
    # THEN it is refused: a caller that only makes sense for one operation kind
    # (e.g. the CLI's add_reaction command) must reject the other kind explicitly.
    with pytest.raises(RequestViolationError, match="add_reaction"):
        parse_changeset(changeset, expected_type="add_reaction")


def test_apply_changeset_dispatches_to_add_reaction(model: cobra.Model) -> None:
    # GIVEN a parsed add_reaction request.
    request = ReactionRequest.from_dict(
        {
            "reaction_id": "NEWRXN",
            "metabolites": {"actp_c": -1, "pi_c": 1},
            "lower_bound": 0.0,
            "upper_bound": 1000.0,
        }
    )
    # WHEN applying it through apply_changeset.
    apply_changeset(model, request)
    # THEN the reaction was added, exactly as add_reaction would have done.
    assert "NEWRXN" in model.reactions


def test_apply_changeset_dispatches_to_delete_reaction(model: cobra.Model) -> None:
    # GIVEN a parsed delete_reaction request.
    request = DeleteReactionRequest.from_dict({"reaction_id": "ACKr"})
    # WHEN applying it through apply_changeset.
    apply_changeset(model, request)
    # THEN the reaction was removed, exactly as delete_reaction would have done.
    assert "ACKr" not in model.reactions
