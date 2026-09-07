"""Behaviour checks for adding a reaction and validating the candidate."""

from __future__ import annotations

import json
from pathlib import Path

import cobra
import pytest
from cobra.io import read_sbml_model, write_sbml_model

from hermes_gem_maintenance import (
    ReactionRequest,
    UncomparableGPRError,
    add_reaction,
    canonical_gpr,
    check_candidate,
    comparable_gpr,
    diff_snapshots,
    semantic_snapshot,
)
from hermes_gem_maintenance import checks as checks_module
from hermes_gem_maintenance.checks import EXCLUDED_FROM_SNAPSHOT
from hermes_gem_maintenance.errors import (
    InsufficientInformationError,
    RequestViolationError,
)

# ==== fixtures ====


@pytest.fixture
def model() -> cobra.Model:
    """Tiny model carrying the metabolites the example reaction needs."""
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
        m.add_metabolites(
            [cobra.Metabolite(mid, formula=formula, charge=charge, compartment="c")]
        )
    existing = cobra.Reaction("ACKr", lower_bound=-1000.0, upper_bound=1000.0)
    m.add_reactions([existing])
    existing.add_metabolites({m.metabolites.actp_c: 1, m.metabolites.pi_c: -1})
    return m


@pytest.fixture
def spec() -> dict[str, object]:
    """The PKETF request: balanced, referencing only existing metabolites."""
    return {
        "reaction_id": "PKETF",
        "name": "Phosphoketolase (fructose-6-phosphate utilizing)",
        "metabolites": {"f6p_c": -1, "pi_c": -1, "actp_c": 1, "e4p_c": 1, "h2o_c": 1},
        "lower_bound": 0.0,
        "upper_bound": 1000.0,
        "gene_reaction_rule": "xfp",
    }


@pytest.fixture
def request_(spec: dict[str, object]) -> ReactionRequest:
    return ReactionRequest.from_dict(spec)


# ==== parsing a request ====


def test_request_rejects_a_definition_missing_required_fields() -> None:
    # GIVEN a definition with no bounds.
    partial = {"reaction_id": "R1", "metabolites": {"a_c": -1}}
    # WHEN parsing it.
    # THEN it reports what is missing, so the caller can ask a specific question.
    with pytest.raises(InsufficientInformationError) as caught:
        ReactionRequest.from_dict(partial)
    assert set(caught.value.context["missing"]) == {"lower_bound", "upper_bound"}


def test_request_rejects_inverted_bounds(spec: dict[str, object]) -> None:
    # GIVEN a definition whose lower bound exceeds its upper bound.
    spec["lower_bound"] = 10.0
    spec["upper_bound"] = 1.0
    # WHEN parsing it.
    # THEN it is a violation, not missing information; more facts would not help.
    with pytest.raises(RequestViolationError, match="lower bound"):
        ReactionRequest.from_dict(spec)


def test_request_rejects_a_zero_coefficient(spec: dict[str, object]) -> None:
    # GIVEN a stoichiometry containing a zero coefficient.
    spec["metabolites"] = {"f6p_c": -1, "pi_c": 0}
    # WHEN parsing it.
    # THEN it fails; a zero coefficient silently drops a participant.
    with pytest.raises(RequestViolationError, match="non-zero"):
        ReactionRequest.from_dict(spec)


# ==== adding reactions ====


def test_add_reaction_applies_the_requested_definition(
    model: cobra.Model, request_: ReactionRequest
) -> None:
    # GIVEN a model without PKETF.
    # WHEN adding the requested reaction.
    added = add_reaction(model, request_)
    # THEN stoichiometry, bounds and gene rule come from the request, not defaults.
    assert {m.id: c for m, c in added.metabolites.items()} == dict(request_.metabolites)
    assert added.bounds == (0.0, 1000.0)
    assert added.gene_reaction_rule == "xfp"


def test_add_reaction_refuses_an_existing_identifier(
    model: cobra.Model, spec: dict[str, object]
) -> None:
    # GIVEN a request reusing ACKr, an identifier the model already has.
    spec["reaction_id"] = "ACKr"
    # WHEN adding it.
    # THEN it fails; silently overwriting a curated reaction would corrupt the model.
    with pytest.raises(RequestViolationError, match="already exists"):
        add_reaction(model, ReactionRequest.from_dict(spec))


def test_add_reaction_refuses_unknown_metabolites(
    model: cobra.Model, spec: dict[str, object]
) -> None:
    # GIVEN a request naming a metabolite absent from the model.
    spec["metabolites"] = {"f6p_c": -1, "nonexistent_c": 1}
    # WHEN adding it.
    # THEN it fails rather than inventing the metabolite.
    with pytest.raises(RequestViolationError, match="absent from the model"):
        add_reaction(model, ReactionRequest.from_dict(spec))


def test_add_reaction_leaves_the_base_model_untouched(
    model: cobra.Model, request_: ReactionRequest
) -> None:
    # GIVEN a snapshot of the model before any change.
    before = semantic_snapshot(model)
    # WHEN adding the reaction to a copy.
    add_reaction(model.copy(), request_)
    # THEN the original is unchanged; candidates must never mutate the input.
    assert semantic_snapshot(model) == before


# ==== checking candidates ====


def test_check_passes_for_a_faithful_candidate(
    model: cobra.Model, request_: ReactionRequest
) -> None:
    # GIVEN a candidate built exactly from the request.
    candidate = model.copy()
    add_reaction(candidate, request_)
    # WHEN checking it against the base model.
    result = check_candidate(model, candidate, request_)
    # THEN nothing fails and the balance check reaches a real verdict.
    assert result.ok
    assert not result.unverifiable
    assert any("balanced" in line for line in result.passed)


def test_check_detects_stoichiometry_that_ignores_the_request(
    model: cobra.Model, request_: ReactionRequest
) -> None:
    # GIVEN a candidate whose coefficients differ from what was asked.
    candidate = model.copy()
    add_reaction(candidate, request_)
    candidate.reactions.get_by_id("PKETF").add_metabolites(
        {candidate.metabolites.h2o_c: 1}
    )
    # WHEN checking it.
    result = check_candidate(model, candidate, request_)
    # THEN the mismatch is reported instead of being smoothed over.
    assert not result.ok
    assert any("stoichiometry" in line for line in result.failed)


def test_check_detects_an_unbalanced_reaction(
    model: cobra.Model, spec: dict[str, object]
) -> None:
    # GIVEN a request whose coefficients violate elemental conservation.
    spec["metabolites"] = {"f6p_c": -1, "pi_c": -1, "actp_c": 1, "e4p_c": 1}
    unbalanced = ReactionRequest.from_dict(spec)
    candidate = model.copy()
    add_reaction(candidate, unbalanced)
    # WHEN checking it.
    result = check_candidate(model, candidate, unbalanced)
    # THEN the imbalance fails the check; the missing water is a real error.
    assert not result.ok
    assert any("balance" in line for line in result.failed)


def test_check_reports_balance_as_unverifiable_without_metadata(
    model: cobra.Model, request_: ReactionRequest
) -> None:
    # GIVEN a participant lacking a formula.
    model.metabolites.e4p_c.formula = None
    candidate = model.copy()
    add_reaction(candidate, request_)
    # WHEN checking it.
    result = check_candidate(model, candidate, request_)
    # THEN balance is unverifiable, never silently counted as passed.
    assert any("balance" in line for line in result.unverifiable)
    assert not any("balance" in line for line in result.passed)


def test_check_detects_changes_beyond_the_request(
    model: cobra.Model, request_: ReactionRequest
) -> None:
    # GIVEN a candidate that also alters an unrelated reaction's bounds.
    candidate = model.copy()
    add_reaction(candidate, request_)
    candidate.reactions.get_by_id("ACKr").bounds = (0.0, 10.0)
    # WHEN checking it.
    result = check_candidate(model, candidate, request_)
    # THEN the collateral edit is reported; only the requested change is acceptable.
    assert not result.ok
    assert any("no unrelated semantic changes" in line for line in result.failed)


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
    # GIVEN two rules differing only in grouping or operand order.
    # WHEN canonicalizing both.
    # THEN they compare equal; COBRApy rewrites grouping on every SBML write.
    assert canonical_gpr(left) == canonical_gpr(right)


def test_canonical_gpr_still_separates_different_logic() -> None:
    # GIVEN rules whose boolean meaning genuinely differs.
    # WHEN canonicalizing.
    # THEN they stay distinct; the comparison must not flatten real changes away.
    assert canonical_gpr("a and b") != canonical_gpr("a or b")
    assert canonical_gpr("a and b") != canonical_gpr("a and c")


def test_snapshot_diff_ignores_gene_rule_reformatting(
    model: cobra.Model, request_: ReactionRequest
) -> None:
    # GIVEN a model whose gene rule is rewritten with equivalent grouping.
    add_reaction(model, request_)
    model.reactions.get_by_id("ACKr").gene_reaction_rule = "(g1 and g2) and g3"
    before = semantic_snapshot(model)
    model.reactions.get_by_id("ACKr").gene_reaction_rule = "g1 and g2 and g3"
    # WHEN diffing the snapshots.
    # THEN no change is reported; this rewrite is what SBML roundtripping produces.
    assert diff_snapshots(before, semantic_snapshot(model)) == {}


def test_check_accepts_a_gene_rule_regrouped_by_sbml(
    model: cobra.Model, spec: dict[str, object], tmp_path: Path
) -> None:
    # GIVEN a request whose gene rule carries redundant parentheses.
    spec["gene_reaction_rule"] = "(g1 and g2) and g3"
    grouped = ReactionRequest.from_dict(spec)
    candidate = model.copy()
    add_reaction(candidate, grouped)
    # WHEN the candidate is written and read back, as export re-checking does.
    path = tmp_path / "candidate.xml"
    write_sbml_model(candidate, str(path))
    reloaded = read_sbml_model(str(path))
    # THEN the check still passes: COBRApy rewrote the grouping, not the logic.
    stored = reloaded.reactions.get_by_id("PKETF").gene_reaction_rule
    assert stored != grouped.gene_reaction_rule
    result = check_candidate(model, reloaded, grouped)
    assert not any("gene rule" in line for line in result.failed)


def test_unreducible_gene_rules_are_never_equal_to_each_other() -> None:
    # GIVEN two rules that do not reduce to boolean logic, naming different genes.
    # (Regression: a `str(node)` fallback returned `ast.dump`-style text carrying a
    # memory address, so the value was unstable across runs and every rule COBRApy
    # failed to parse collapsed to a single shared value.)
    # WHEN each is put through the total comparison the snapshot uses.
    left, right = comparable_gpr("gA and not gB"), comparable_gpr("gZ and not gY")
    # THEN they stay distinct, and distinct from a rule that does reduce.
    assert left != right
    assert left != comparable_gpr("gA and gB")


def test_canonical_gpr_refuses_a_rule_it_cannot_reduce() -> None:
    # GIVEN rules COBRApy either fails to parse or parses but cannot evaluate.
    # WHEN canonicalizing them.
    # THEN each is refused rather than silently folded into a shared value.
    for rule in ("not gA", "gA > gB", "gA - gB"):
        with pytest.raises(UncomparableGPRError):
            canonical_gpr(rule)


def test_snapshot_diff_reports_an_edit_to_a_gene_rule(
    model: cobra.Model, request_: ReactionRequest
) -> None:
    # GIVEN an untouched reaction whose gene rule is swapped for a different one.
    add_reaction(model, request_)
    model.reactions.get_by_id("ACKr").gene_reaction_rule = "gA and gB"
    before = semantic_snapshot(model)
    model.reactions.get_by_id("ACKr").gene_reaction_rule = "gZ and gY"
    # WHEN diffing the snapshots.
    # THEN the edit is reported.
    assert diff_snapshots(before, semantic_snapshot(model)) != {}


def test_a_gene_rule_that_cannot_be_reduced_is_undecided_not_passed(
    model: cobra.Model,
    request_: ReactionRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # GIVEN a reduction that refuses the rule in front of it. COBRApy's own getter
    # cannot currently produce such a rule -- GPR() rejects the AST and the setter
    # stores an empty rule -- so the refusal is forced here. The check exists as
    # policy: every deliberate outcome of a public entry point belongs to the error
    # taxonomy or to a check verdict, never an escaping ValueError.
    candidate = model.copy()
    add_reaction(candidate, request_)

    def refuse(rule: str) -> object:
        raise UncomparableGPRError(rule)

    monkeypatch.setattr(checks_module, "canonical_gpr", refuse)
    # WHEN checking the candidate.
    result = check_candidate(model, candidate, request_)
    # THEN the gene rule is undecided rather than passed, failed, or a traceback.
    assert any("gene rule" in line for line in result.unverifiable)
    assert not any("gene rule" in line for line in result.passed)
    assert not any("gene rule" in line for line in result.failed)


def test_passing_check_states_what_it_did_not_compare(
    model: cobra.Model, request_: ReactionRequest
) -> None:
    # GIVEN a clean candidate.
    # (Regression: EXCLUDED_FROM_SNAPSHOT declared itself the contract but appeared
    # in no output, so a caller reading `passed` could not see its boundary.)
    candidate = model.copy()
    add_reaction(candidate, request_)
    # WHEN checking it.
    result = check_candidate(model, candidate, request_)
    # THEN the verdict names the fields it does not cover.
    unrelated = next(
        line for line in result.passed if "no unrelated semantic changes" in line
    )
    for excluded in EXCLUDED_FROM_SNAPSHOT:
        assert excluded in unrelated


# ==== review regressions ====


def test_unverifiable_balance_is_not_reported_as_passed(
    model: cobra.Model, request_: ReactionRequest
) -> None:
    # GIVEN a participant with no formula, so mass balance cannot be computed.
    # (Regression: `ok` returned True whenever `failed` was empty, so a candidate
    # that was never balance-checked was delivered as verified.)
    model.metabolites.get_by_id("f6p_c").formula = None
    candidate = model.copy()
    add_reaction(candidate, request_)
    # WHEN checking the candidate.
    result = check_candidate(model, candidate, request_)
    # THEN the verdict is a third state: not failed, but not deliverable either.
    assert result.unverifiable
    assert result.status == "unverifiable"
    assert result.ok is False
    assert result.blocked is False


def test_check_rejects_a_name_the_request_did_not_ask_for(
    model: cobra.Model, request_: ReactionRequest
) -> None:
    # GIVEN a candidate whose new reaction carries a name other than the requested one.
    # (Regression: name and subsystem were never compared, so the delivered reaction
    # could be labelled as anything.)
    candidate = model.copy()
    add_reaction(candidate, request_)
    candidate.reactions.get_by_id("PKETF").name = "Not what was requested"
    # WHEN checking the candidate.
    result = check_candidate(model, candidate, request_)
    # THEN the mismatch is a failure, not a silently accepted edit.
    assert any("name matches request" in line for line in result.failed)


def test_check_rejects_a_gene_rule_when_the_request_had_none(
    model: cobra.Model, spec: dict[str, object]
) -> None:
    # GIVEN a request that specifies no gene rule at all.
    # (Regression: the comparison was skipped when the request field was empty, so a
    # candidate could invent gene associations the requester never authorised.)
    del spec["gene_reaction_rule"]
    request = ReactionRequest.from_dict(spec)
    candidate = model.copy()
    add_reaction(candidate, request)
    candidate.reactions.get_by_id("PKETF").gene_reaction_rule = "unrequested_gene"
    # WHEN checking the candidate.
    result = check_candidate(model, candidate, request)
    # THEN the invented rule fails the check.
    assert any("gene rule matches request" in line for line in result.failed)


def test_check_detects_an_edit_to_an_untouched_reaction_name(
    model: cobra.Model, request_: ReactionRequest
) -> None:
    # GIVEN a candidate that also renames a reaction the request never mentioned.
    # (Regression: the snapshot omitted names, so the "nothing else changed" claim
    # asserted something the comparison could not see.)
    candidate = model.copy()
    add_reaction(candidate, request_)
    candidate.reactions.get_by_id("ACKr").name = "Silently renamed"
    # WHEN checking the candidate.
    result = check_candidate(model, candidate, request_)
    # THEN the unrelated edit is reported.
    assert any("no unrelated semantic changes" in line for line in result.failed)


def test_subsystem_the_writer_discards_is_unverifiable_not_failed(
    model: cobra.Model, spec: dict[str, object], tmp_path: Path
) -> None:
    # GIVEN a request naming a subsystem, written to a model whose SBML does not
    # carry subsystem annotations. (Regression: comparing it as a plain equality
    # reported the serializer's format as a violation by the candidate, which would
    # block delivery of a correct reaction.)
    spec["subsystem"] = "Heterologous pathway"
    request = ReactionRequest.from_dict(spec)
    candidate = model.copy()
    add_reaction(candidate, request)
    path = tmp_path / "candidate.xml"
    write_sbml_model(candidate, str(path))
    reloaded = read_sbml_model(str(path))
    # WHEN checking the reloaded candidate.
    result = check_candidate(model, reloaded, request)
    # THEN the loss is undecidable, not a failure: nothing contradicts the request.
    assert reloaded.reactions.get_by_id("PKETF").subsystem == ""
    assert any("subsystem" in line for line in result.unverifiable)
    assert not any("subsystem" in line for line in result.failed)


def test_subsystem_changed_to_something_else_still_fails(
    model: cobra.Model, spec: dict[str, object]
) -> None:
    # GIVEN a candidate whose subsystem is present but not what was requested.
    spec["subsystem"] = "Heterologous pathway"
    request = ReactionRequest.from_dict(spec)
    candidate = model.copy()
    add_reaction(candidate, request)
    candidate.reactions.get_by_id("PKETF").subsystem = "Something else entirely"
    # WHEN checking it.
    result = check_candidate(model, candidate, request)
    # THEN it fails: a stored value that disagrees is a real contradiction, and the
    # unverifiable path must not become a way to ignore the field.
    assert any("subsystem" in line for line in result.failed)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("metabolites", [["f6p_c", -1]]),
        ("metabolites", "f6p_c"),
        ("reaction_id", ""),
        ("lower_bound", "not-a-number"),
        ("upper_bound", float("inf")),
        ("name", 42),
    ],
)
def test_malformed_definitions_raise_structured_errors(
    spec: dict[str, object], field: str, value: object
) -> None:
    # GIVEN a definition whose field has the wrong type or an unusable value.
    # (Regression: a list of metabolites escaped as a bare AttributeError, so the
    # documented error taxonomy did not hold at the package's own boundary.)
    spec[field] = value
    # WHEN building the request.
    # THEN it fails inside the taxonomy rather than leaking a Python type error.
    with pytest.raises((RequestViolationError, InsufficientInformationError)):
        ReactionRequest.from_dict(spec)


def test_check_rejects_a_candidate_that_added_nothing(
    model: cobra.Model, request_: ReactionRequest
) -> None:
    # GIVEN a baseline that already contains the requested reaction, and a candidate
    # that is an unchanged copy of it. (Regression: `check` only asked whether the
    # reaction was present in the candidate, so a copy that added nothing passed
    # every check and reported "only the requested reaction added". `check` and
    # `export` are public entry points and cannot assume `add_reaction` ran first.)
    seeded = model.copy()
    add_reaction(seeded, request_)
    candidate = seeded.copy()
    # WHEN checking it against that baseline.
    result = check_candidate(seeded, candidate, request_)
    # THEN it fails: the operation is "add", so presence in the baseline is a
    # contradiction, not a success.
    assert not result.ok
    assert any("absent from baseline" in line for line in result.failed)


def test_check_rejects_an_extra_reaction_alongside_the_requested_one(
    model: cobra.Model, request_: ReactionRequest
) -> None:
    # GIVEN a candidate that adds the requested reaction and one extra reaction.
    candidate = model.copy()
    add_reaction(candidate, request_)
    extra = cobra.Reaction("SNEAKY", lower_bound=0.0, upper_bound=1000.0)
    candidate.add_reactions([extra])
    extra.add_metabolites({candidate.metabolites.get_by_id("h2o_c"): -1})
    # WHEN checking it.
    result = check_candidate(model, candidate, request_)
    # THEN the extra addition fails the invariant.
    assert any("no unrelated semantic changes" in line for line in result.failed)


def test_malformed_gene_rule_is_a_request_violation(
    spec: dict[str, object],
) -> None:
    # GIVEN a gene rule that is not a parsable boolean expression.
    # (Regression: COBRApy's setter logs a traceback and stores an empty rule, so the
    # package built a candidate whose gene association had silently vanished and only
    # failed later as a validation error -- blaming the candidate for a syntax error
    # in the request.)
    spec["gene_reaction_rule"] = "geneA and"
    # WHEN building the request.
    # THEN it is refused at the boundary, in the right category.
    with pytest.raises(RequestViolationError, match="parsable"):
        ReactionRequest.from_dict(spec)


def test_valid_gene_rules_are_still_accepted(spec: dict[str, object]) -> None:
    # GIVEN gene rules that are legitimate boolean expressions.
    for rule in ("xfp", "a and b", "a or (b and c)", "(a and b) and c"):
        spec["gene_reaction_rule"] = rule
        # WHEN building the request.
        # THEN it succeeds; the syntax check must not reject working input.
        assert ReactionRequest.from_dict(spec).gene_reaction_rule == rule


# ==== example data ====


def test_example_reaction_matches_the_frozen_model() -> None:
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
