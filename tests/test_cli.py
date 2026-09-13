"""Behaviour checks for the command-line interface.

These exercise the commands an agent actually calls, including the contract that
matters most: a failing candidate must not produce a deliverable.
"""

from __future__ import annotations

import json
from pathlib import Path

import cobra
import pytest
from cobra.io import write_sbml_model

from hermes_gem_maintenance import publish as publish_module
from hermes_gem_maintenance.changes import ReactionRequest
from hermes_gem_maintenance.cli import Cli
from hermes_gem_maintenance.consistency_review import (
    ConsistencyRegression,
    ConsistencyReview,
    ConsistencySnapshot,
    review_consistency,
)
from hermes_gem_maintenance.errors import (
    InsufficientInformationError,
    ModelIntegrityError,
    RequestViolationError,
    ValidationFailedError,
)
from hermes_gem_maintenance.inspect import require_unique_metabolite
from hermes_gem_maintenance.model_io import file_digest, load_model
from hermes_gem_maintenance.publish import build_candidate, publish_deliverable


def _changeset_file(path: Path, operation: dict[str, object]) -> Path:
    """Write one operation wrapped in the envelope every changeset file carries."""
    path.write_text(
        json.dumps({"operations": [operation]}), encoding="utf-8"
    )
    return path

# ==== fixtures ====


@pytest.fixture
def baseline(tmp_path: Path) -> Path:
    """A small SBML model on disk, standing in for the frozen input."""
    model = cobra.Model("toy")
    model.compartments = {"c": "cytosol", "p": "periplasm"}
    # aa_c contains a_c, and one name is shared across compartments: the two shapes
    # that separate "weaker matches also hit" from "genuinely ambiguous".
    specs = [
        ("a_c", "c", "Alpha", "C6H11O9P"),
        ("aa_c", "c", "Double alpha", "C6H11O9P"),
        ("b_c", "c", "Beta", "C6H11O9P"),
        ("b_p", "p", "Beta", "C6H11O9P"),
    ]
    for mid, compartment, name, formula in specs:
        model.add_metabolites(
            [
                cobra.Metabolite(
                    mid,
                    name=name,
                    formula=formula,
                    charge=0,
                    compartment=compartment,
                )
            ]
        )
    existing = cobra.Reaction("EXIST", lower_bound=0.0, upper_bound=1000.0)
    model.add_reactions([existing])
    existing.add_metabolites({model.metabolites.a_c: -1})
    # Exchanges giving a_c a source and b_c a sink. Without them the model has no
    # way to carry flux at all, so any reaction later added by a test (a_c -> b_c)
    # is universally blocked regardless of whether the addition itself is sound --
    # not a signal about the change, just an artifact of a toy network with no
    # open boundary. The real screening criteria (gem-model-modification skill)
    # already require an existing consumer/producer for a real case; this mirrors
    # that requirement in miniature so the consistency-regression gate has
    # something meaningful to say about the reactions these tests actually add.
    ex_a = cobra.Reaction("EX_a_c", lower_bound=-1000.0, upper_bound=1000.0)
    model.add_reactions([ex_a])
    ex_a.add_metabolites({model.metabolites.a_c: -1})
    ex_b = cobra.Reaction("EX_b_c", lower_bound=-1000.0, upper_bound=1000.0)
    model.add_reactions([ex_b])
    ex_b.add_metabolites({model.metabolites.b_c: -1})
    path = tmp_path / "baseline.xml"
    write_sbml_model(model, str(path))
    return path


@pytest.fixture
def request_file(tmp_path: Path) -> Path:
    """A balanced, unambiguous add_reaction changeset."""
    return _changeset_file(
        tmp_path / "changeset.json",
        {
            "type": "add_reaction",
            "reaction_id": "NEWRXN",
            "metabolites": {"a_c": -1, "b_c": 1},
            "lower_bound": 0.0,
            "upper_bound": 1000.0,
            "gene_reaction_rule": "geneA",
        },
    )


@pytest.fixture
def deletable_baseline(tmp_path: Path) -> Path:
    """A baseline where EXIST has a redundant twin, so deleting EXIST is clean.

    `baseline` makes EXIST the sole consumer of a_c, so removing it strands a_c's
    exchange reaction with nothing else to balance against -- MEMOTE correctly
    reports that as a newly blocked reaction, which is the right answer but not
    what a "happy path" delete test needs. This fixture adds a second reaction
    (BYPASS) with the same effect as EXIST, so EXIST is deletable without isolating
    anything: exactly the kind of redundancy the gem-model-modification skill's
    screening criteria require of a real deletion case (S11.4 of the plan).
    """
    model = cobra.Model("toy")
    model.compartments = {"c": "cytosol", "p": "periplasm"}
    specs = [
        ("a_c", "c", "Alpha", "C6H11O9P"),
        ("aa_c", "c", "Double alpha", "C6H11O9P"),
        ("b_c", "c", "Beta", "C6H11O9P"),
        ("b_p", "p", "Beta", "C6H11O9P"),
    ]
    for mid, compartment, name, formula in specs:
        model.add_metabolites(
            [
                cobra.Metabolite(
                    mid,
                    name=name,
                    formula=formula,
                    charge=0,
                    compartment=compartment,
                )
            ]
        )
    existing = cobra.Reaction("EXIST", lower_bound=0.0, upper_bound=1000.0)
    model.add_reactions([existing])
    existing.add_metabolites({model.metabolites.a_c: -1})
    bypass = cobra.Reaction("BYPASS", lower_bound=0.0, upper_bound=1000.0)
    model.add_reactions([bypass])
    bypass.add_metabolites({model.metabolites.a_c: -1})
    ex_a = cobra.Reaction("EX_a_c", lower_bound=-1000.0, upper_bound=1000.0)
    model.add_reactions([ex_a])
    ex_a.add_metabolites({model.metabolites.a_c: -1})
    ex_b = cobra.Reaction("EX_b_c", lower_bound=-1000.0, upper_bound=1000.0)
    model.add_reactions([ex_b])
    ex_b.add_metabolites({model.metabolites.b_c: -1})
    path = tmp_path / "deletable_baseline.xml"
    write_sbml_model(model, str(path))
    return path


# ==== inspect and resolve ====


def test_inspect_reports_absence_rather_than_failing(baseline: Path) -> None:
    # GIVEN an identifier the model does not contain.
    # WHEN inspecting it.
    payload = json.loads(Cli().inspect(model=str(baseline), reaction="NOPE"))
    # THEN absence is reported as data; not finding something is a normal answer.
    assert payload == {"absent": "NOPE", "kind": "reaction"}


def test_resolve_settles_an_exact_identifier_despite_weaker_matches(
    baseline: Path,
) -> None:
    # GIVEN a query that hits one identifier exactly and another as a substring.
    # WHEN resolving it.
    payload = json.loads(Cli().resolve(model=str(baseline), query="a_c"))
    # THEN the exact hit settles the query. Reporting this as ambiguous because more
    # than one candidate came back would block every short identifier.
    assert payload["count"] > 1
    assert payload["unambiguous"] is True
    assert payload["resolved_id"] == "a_c"


def test_resolve_refuses_a_name_shared_across_compartments(baseline: Path) -> None:
    # GIVEN a name carried by metabolites in two compartments.
    # WHEN resolving it.
    payload = json.loads(Cli().resolve(model=str(baseline), query="Beta"))
    # THEN no winner is offered: the request never said which compartment.
    assert payload["unambiguous"] is False
    assert payload["resolved_id"] is None


def test_resolve_agrees_with_the_library_verdict(baseline: Path) -> None:
    # GIVEN queries spanning both shapes: exact-plus-noise, and true ambiguity.
    loaded = load_model(baseline)
    # WHEN each is judged by the CLI and by require_unique_metabolite.
    for query in ("a_c", "Beta", "b_p"):
        payload = json.loads(Cli().resolve(model=str(baseline), query=query))
        try:
            expected = require_unique_metabolite(loaded, query)["id"]
        except InsufficientInformationError:
            expected = None
        # THEN they never disagree. Two definitions of "unambiguous" in one package
        # means the CLI tells an agent to ask a question the API would not have asked.
        assert payload["resolved_id"] == expected, query


# ==== add-reaction ====


def test_add_reaction_writes_a_candidate_and_preserves_the_baseline(
    baseline: Path, request_file: Path, tmp_path: Path
) -> None:
    # GIVEN a baseline model and its digest before the command runs.
    before = file_digest(baseline)
    output = tmp_path / "candidate.xml"
    # WHEN adding the reaction.
    payload = json.loads(
        Cli().add_reaction(
            model=str(baseline), changeset=str(request_file), output=str(output)
        )
    )
    # THEN a candidate exists and the baseline bytes are identical.
    assert output.exists()
    assert payload["baseline_sha256"] == before
    assert file_digest(baseline) == before


def test_add_reaction_refuses_to_write_over_the_baseline(
    baseline: Path, request_file: Path
) -> None:
    # GIVEN an output path pointing at the baseline itself.
    before = file_digest(baseline)
    # WHEN adding the reaction there.
    # THEN it refuses and the baseline is untouched.
    with pytest.raises(ModelIntegrityError, match="baseline"):
        Cli().add_reaction(
            model=str(baseline), changeset=str(request_file), output=str(baseline)
        )
    assert file_digest(baseline) == before


def test_add_reaction_distinguishes_a_violation_from_missing_information(
    baseline: Path, tmp_path: Path
) -> None:
    # GIVEN one request that conflicts with the model and one that is incomplete.
    conflict = _changeset_file(
        tmp_path / "conflict.json",
        {
            "type": "add_reaction",
            "reaction_id": "EXIST",
            "metabolites": {"a_c": -1, "b_c": 1},
            "lower_bound": 0.0,
            "upper_bound": 1000.0,
        },
    )
    incomplete = _changeset_file(
        tmp_path / "incomplete.json",
        {"type": "add_reaction", "reaction_id": "NEWRXN", "metabolites": {"a_c": -1}},
    )
    # WHEN submitting each.
    # THEN the categories differ: one needs a different request, one needs more facts.
    with pytest.raises(RequestViolationError):
        Cli().add_reaction(
            model=str(baseline),
            changeset=str(conflict),
            output=str(tmp_path / "a.xml"),
        )
    with pytest.raises(InsufficientInformationError):
        Cli().add_reaction(
            model=str(baseline),
            changeset=str(incomplete),
            output=str(tmp_path / "b.xml"),
        )


# ==== delete-reaction ====


def test_delete_reaction_writes_a_candidate_and_preserves_the_baseline(
    baseline: Path, tmp_path: Path
) -> None:
    # GIVEN a baseline model containing EXIST, and its digest before the command runs.
    before = file_digest(baseline)
    delete_spec = _changeset_file(
        tmp_path / "delete.json", {"type": "delete_reaction", "reaction_id": "EXIST"}
    )
    output = tmp_path / "candidate.xml"
    # WHEN deleting the reaction.
    payload = json.loads(
        Cli().delete_reaction(
            model=str(baseline), changeset=str(delete_spec), output=str(output)
        )
    )
    # THEN a candidate exists, the reaction is gone, and the baseline is untouched.
    assert output.exists()
    assert "EXIST" not in load_model(output).reactions
    assert payload["baseline_sha256"] == before
    assert file_digest(baseline) == before


def test_delete_reaction_refuses_an_identifier_absent_from_the_model(
    baseline: Path, tmp_path: Path
) -> None:
    # GIVEN a request naming a reaction the baseline does not have.
    delete_spec = _changeset_file(
        tmp_path / "delete.json", {"type": "delete_reaction", "reaction_id": "NOPE"}
    )
    # WHEN deleting it.
    # THEN it is refused as a request violation, not written as an empty diff.
    with pytest.raises(RequestViolationError, match="does not exist"):
        Cli().delete_reaction(
            model=str(baseline),
            changeset=str(delete_spec),
            output=str(tmp_path / "candidate.xml"),
        )


def test_check_and_export_accept_a_deletion_via_its_changeset_type(
    deletable_baseline: Path, tmp_path: Path
) -> None:
    # GIVEN a candidate produced by delete_reaction, on a baseline where EXIST has a
    # redundant twin (BYPASS) so removing it does not strand anything else.
    delete_spec = _changeset_file(
        tmp_path / "delete.json", {"type": "delete_reaction", "reaction_id": "EXIST"}
    )
    candidate = tmp_path / "candidate.xml"
    Cli().delete_reaction(
        model=str(deletable_baseline),
        changeset=str(delete_spec),
        output=str(candidate),
    )
    # WHEN checking it. The changeset's own operation type ("delete_reaction")
    # selects the invariant; there is no separate argument that could disagree
    # with it.
    checked = json.loads(
        Cli().check(
            model=str(deletable_baseline),
            candidate=str(candidate),
            changeset=str(delete_spec),
        )
    )
    # THEN it passes, using the removal invariant rather than the addition one.
    assert checked["status"] == "passed"
    assert checked["reaction_id"] == "EXIST"

    # WHEN exporting it with the same changeset.
    delivered = tmp_path / "delivered.xml"
    exported = json.loads(
        Cli().export(
            model=str(deletable_baseline),
            candidate=str(candidate),
            changeset=str(delete_spec),
            output=str(delivered),
        )
    )
    # THEN it is delivered, and MEMOTE ran (this baseline is small enough that it
    # genuinely executes) with a clean consistency regression.
    assert delivered.exists()
    assert exported["status"] == "passed"
    assert exported["consistency_regression"]["ok"] is True


def test_check_rejects_an_add_candidate_when_asked_to_check_a_deletion(
    baseline: Path, request_file: Path, tmp_path: Path
) -> None:
    # GIVEN a candidate produced by add_reaction (adds NEWRXN, EXIST still present).
    candidate = tmp_path / "candidate.xml"
    Cli().add_reaction(
        model=str(baseline), changeset=str(request_file), output=str(candidate)
    )
    # WHEN checking it against a delete_reaction changeset naming a reaction that
    # was never removed this way.
    delete_spec = _changeset_file(
        tmp_path / "delete.json", {"type": "delete_reaction", "reaction_id": "EXIST"}
    )
    payload = json.loads(
        Cli().check(
            model=str(baseline),
            candidate=str(candidate),
            changeset=str(delete_spec),
        )
    )
    # THEN it fails: EXIST is still present in the candidate, so the requested
    # removal never happened -- checking against the wrong changeset does not
    # coincidentally pass.
    assert payload["status"] == "failed"
    assert any("absent from candidate" in line for line in payload["failed"])


def test_changeset_refuses_an_operation_type_this_package_does_not_implement(
    baseline: Path, tmp_path: Path
) -> None:
    # GIVEN a changeset naming an operation type this package does not implement.
    bogus = _changeset_file(
        tmp_path / "bogus.json", {"type": "rename_reaction", "reaction_id": "EXIST"}
    )
    # WHEN checking it.
    # THEN it is refused, naming the unsupported type, rather than silently treated
    # as one of the implemented kinds.
    with pytest.raises(RequestViolationError, match="rename_reaction"):
        Cli().check(
            model=str(baseline),
            candidate=str(baseline),
            changeset=str(bogus),
        )


# ==== check and export ====


def test_check_passes_a_candidate_produced_by_add_reaction(
    baseline: Path, request_file: Path, tmp_path: Path
) -> None:
    # GIVEN a candidate written by the add command and reloaded from disk.
    candidate = tmp_path / "candidate.xml"
    Cli().add_reaction(
        model=str(baseline), changeset=str(request_file), output=str(candidate)
    )
    # WHEN checking it.
    payload = json.loads(
        Cli().check(
            model=str(baseline),
            candidate=str(candidate),
            changeset=str(request_file),
        )
    )
    # THEN it passes; the roundtrip through SBML must not invalidate a good candidate.
    assert payload["status"] == "passed"
    assert payload["failed"] == []
    # AND the payload names its own scope: the CLI's `check` command never runs
    # MEMOTE, so this passing result must not be read as "export would also pass".
    assert payload["scope"] == "structural"


def test_check_records_semantic_diff_and_local_checks_without_changing_stdout(
    baseline: Path, request_file: Path, tmp_path: Path
) -> None:
    # GIVEN a candidate that passes the existing check and a recording directory.
    candidate = tmp_path / "candidate.xml"
    Cli().add_reaction(
        model=str(baseline), changeset=str(request_file), output=str(candidate)
    )
    record_directory = tmp_path / "record"
    record_directory.mkdir()
    # WHEN checking once with recording and once without it.
    recorded = json.loads(
        Cli().check(
            model=str(baseline),
            candidate=str(candidate),
            changeset=str(request_file),
            record_directory=str(record_directory),
        )
    )
    plain = json.loads(
        Cli().check(
            model=str(baseline), candidate=str(candidate), changeset=str(request_file)
        )
    )
    # THEN the public payload is unchanged and both artifacts contain complete data.
    assert recorded == plain
    semantic_diff = json.loads(
        (record_directory / "semantic_diff.json").read_text(encoding="utf-8")
    )
    assert semantic_diff["reactions"]["added"] == ["NEWRXN"]
    assert json.loads(
        (record_directory / "local_checks.json").read_text(encoding="utf-8")
    ) == recorded


def test_check_records_a_failed_local_verdict(
    baseline: Path, request_file: Path, tmp_path: Path
) -> None:
    # GIVEN a candidate whose requested stoichiometry no longer matches its bytes.
    candidate = tmp_path / "candidate.xml"
    Cli().add_reaction(
        model=str(baseline), changeset=str(request_file), output=str(candidate)
    )
    changed = json.loads(request_file.read_text(encoding="utf-8"))
    changed["operations"][0]["metabolites"] = {"a_c": -2, "b_c": 1}
    request_file.write_text(json.dumps(changed), encoding="utf-8")
    record_directory = tmp_path / "record"
    record_directory.mkdir()
    # WHEN checking with recording enabled.
    payload = json.loads(
        Cli().check(
            model=str(baseline),
            candidate=str(candidate),
            changeset=str(request_file),
            record_directory=str(record_directory),
        )
    )
    # THEN both completed check artifacts retain the failed verdict and its diff.
    assert payload["status"] == "failed"
    assert payload["failed"]
    assert json.loads(
        (record_directory / "local_checks.json").read_text(encoding="utf-8")
    ) == payload
    assert json.loads(
        (record_directory / "semantic_diff.json").read_text(encoding="utf-8")
    )["reactions"]["added"] == ["NEWRXN"]


def test_check_refuses_to_overwrite_an_existing_record_artifact(
    baseline: Path, request_file: Path, tmp_path: Path
) -> None:
    # GIVEN a valid candidate and an existing semantic diff artifact.
    candidate = tmp_path / "candidate.xml"
    Cli().add_reaction(
        model=str(baseline), changeset=str(request_file), output=str(candidate)
    )
    record_directory = tmp_path / "record"
    record_directory.mkdir()
    existing = record_directory / "semantic_diff.json"
    existing.write_text("prior evidence", encoding="utf-8")
    # WHEN check tries to record its artifacts.
    with pytest.raises(ModelIntegrityError, match="already exists"):
        Cli().check(
            model=str(baseline),
            candidate=str(candidate),
            changeset=str(request_file),
            record_directory=str(record_directory),
        )
    # THEN the prior artifact remains byte-for-byte unchanged.
    assert existing.read_text(encoding="utf-8") == "prior evidence"


def test_check_preflights_all_record_paths_before_writing(
    baseline: Path, request_file: Path, tmp_path: Path
) -> None:
    # GIVEN a valid candidate and only the second check artifact already present.
    candidate = tmp_path / "candidate.xml"
    Cli().add_reaction(
        model=str(baseline), changeset=str(request_file), output=str(candidate)
    )
    record_directory = tmp_path / "record"
    record_directory.mkdir()
    existing = record_directory / "local_checks.json"
    existing.write_text("prior evidence", encoding="utf-8")
    # WHEN check tries to record the pair.
    with pytest.raises(ModelIntegrityError, match="already exists"):
        Cli().check(
            model=str(baseline),
            candidate=str(candidate),
            changeset=str(request_file),
            record_directory=str(record_directory),
        )
    # THEN it writes neither half of a mixed-run pair.
    assert not (record_directory / "semantic_diff.json").exists()
    assert existing.read_text(encoding="utf-8") == "prior evidence"


def test_export_refuses_to_deliver_a_failing_candidate(
    baseline: Path, request_file: Path, tmp_path: Path
) -> None:
    # GIVEN a candidate that no longer matches the request.
    candidate = tmp_path / "candidate.xml"
    Cli().add_reaction(
        model=str(baseline), changeset=str(request_file), output=str(candidate)
    )
    tampered = json.loads(request_file.read_text(encoding="utf-8"))
    tampered["operations"][0]["metabolites"] = {"a_c": -2, "b_c": 1}
    request_file.write_text(json.dumps(tampered), encoding="utf-8")
    delivered = tmp_path / "delivered.xml"
    # WHEN exporting it.
    # THEN delivery is refused and no file is written; a failed check must not ship.
    with pytest.raises(ValidationFailedError, match="re-validation"):
        Cli().export(
            model=str(baseline),
            candidate=str(candidate),
            changeset=str(request_file),
            output=str(delivered),
        )
    assert not delivered.exists()


def test_export_records_a_local_failure_without_memote_or_result(
    baseline: Path, request_file: Path, tmp_path: Path
) -> None:
    # GIVEN a candidate whose requested stoichiometry was changed after generation.
    candidate = tmp_path / "candidate.xml"
    Cli().add_reaction(
        model=str(baseline), changeset=str(request_file), output=str(candidate)
    )
    changed = json.loads(request_file.read_text(encoding="utf-8"))
    changed["operations"][0]["metabolites"] = {"a_c": -2, "b_c": 1}
    request_file.write_text(json.dumps(changed), encoding="utf-8")
    record_directory = tmp_path / "record"
    record_directory.mkdir()
    delivered = record_directory / "result.xml"
    # WHEN export reaches its local validation failure.
    with pytest.raises(ValidationFailedError, match="re-validation"):
        Cli().export(
            model=str(baseline),
            candidate=str(candidate),
            changeset=str(request_file),
            output=str(delivered),
            record_directory=str(record_directory),
        )
    # THEN the failure verdict is durable, while MEMOTE and result.xml are absent.
    summary = json.loads(
        (record_directory / "validation_summary.json").read_text(encoding="utf-8")
    )
    assert summary["status"] == "failed"
    assert summary["scope"] == "structural"
    assert summary["failed"]
    assert summary["reaction_id"] == "NEWRXN"
    assert "baseline_verified_against" in summary
    assert not (record_directory / "memote_before.json").exists()
    assert not (record_directory / "memote_after.json").exists()
    assert not delivered.exists()


def test_export_refuses_a_candidate_whose_checks_could_not_be_decided(
    baseline: Path, request_file: Path, tmp_path: Path
) -> None:
    # GIVEN a baseline whose metabolite has no formula, so mass balance is undecidable.
    # (Regression: export only inspected `failed`, so an unverifiable candidate was
    # written out and reported as passed.)
    stripped = load_model(baseline)
    stripped.metabolites.get_by_id("a_c").formula = None
    unverifiable_baseline = tmp_path / "no-formula.xml"
    write_sbml_model(stripped, str(unverifiable_baseline))
    candidate = tmp_path / "candidate.xml"
    Cli().add_reaction(
        model=str(unverifiable_baseline),
        changeset=str(request_file),
        output=str(candidate),
    )
    delivered = tmp_path / "delivered.xml"
    # WHEN exporting it.
    # THEN delivery is refused: an untested model must not ship as a verified one.
    with pytest.raises(ValidationFailedError, match="could not be fully verified"):
        Cli().export(
            model=str(unverifiable_baseline),
            candidate=str(candidate),
            changeset=str(request_file),
            output=str(delivered),
        )
    assert not delivered.exists()


def test_check_reports_unverifiable_as_its_own_status(
    baseline: Path, request_file: Path, tmp_path: Path
) -> None:
    # GIVEN the same undecidable candidate.
    stripped = load_model(baseline)
    stripped.metabolites.get_by_id("a_c").formula = None
    unverifiable_baseline = tmp_path / "no-formula.xml"
    write_sbml_model(stripped, str(unverifiable_baseline))
    candidate = tmp_path / "candidate.xml"
    Cli().add_reaction(
        model=str(unverifiable_baseline),
        changeset=str(request_file),
        output=str(candidate),
    )
    # WHEN checking it.
    payload = json.loads(
        Cli().check(
            model=str(unverifiable_baseline),
            candidate=str(candidate),
            changeset=str(request_file),
        )
    )
    # THEN the caller can tell "undecided" from both "passed" and "failed".
    assert payload["status"] == "unverifiable"
    assert payload["failed"] == []
    assert payload["unverifiable"]


def test_malformed_definition_becomes_a_structured_error(
    baseline: Path, request_file: Path, tmp_path: Path
) -> None:
    # GIVEN a definition whose metabolites are a list rather than an object.
    # (Regression: this escaped the CLI as a bare AttributeError, so the documented
    # error taxonomy did not hold for hand-written input.)
    malformed = json.loads(request_file.read_text(encoding="utf-8"))
    malformed["operations"][0]["metabolites"] = [["a_c", -1], ["b_c", 1]]
    request_file.write_text(json.dumps(malformed), encoding="utf-8")
    # WHEN adding the reaction.
    # THEN it fails inside the taxonomy, carrying a category the agent can act on.
    with pytest.raises(RequestViolationError) as caught:
        Cli().add_reaction(
            model=str(baseline),
            changeset=str(request_file),
            output=str(tmp_path / "candidate.xml"),
        )
    assert caught.value.as_dict()["category"] == "request_violation"


def test_export_leaves_no_deliverable_when_the_baseline_changes_mid_run(
    baseline: Path, request_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # GIVEN a run during which the baseline is modified after the candidate is
    # written but before the final integrity check. (Regression: the deliverable was
    # saved directly to the output path and verified afterwards, so a detected
    # tampering still left a full-sized file named like a successful result.)
    candidate = tmp_path / "candidate.xml"
    Cli().add_reaction(
        model=str(baseline), changeset=str(request_file), output=str(candidate)
    )
    delivered = tmp_path / "result.xml"
    real_write = publish_module.write_sbml_model

    def write_then_tamper(model: object, path: str) -> None:
        real_write(model, path)
        baseline.write_bytes(baseline.read_bytes() + b"<!-- tampered -->")

    monkeypatch.setattr(publish_module, "write_sbml_model", write_then_tamper)
    # WHEN exporting.
    # THEN the integrity failure is reported and nothing is left at the output path.
    with pytest.raises(ModelIntegrityError):
        Cli().export(
            model=str(baseline),
            candidate=str(candidate),
            changeset=str(request_file),
            output=str(delivered),
        )
    assert not delivered.exists()
    assert not list(tmp_path.glob(".*.partial"))


def test_export_verifies_the_baseline_against_a_source_manifest(
    baseline: Path, request_file: Path, tmp_path: Path
) -> None:
    # GIVEN a manifest recording a digest the baseline does not have, standing in for
    # a frozen input that drifted before the command started. (Regression: the CLI
    # digested the file it was given and compared it to itself, proving only that
    # nothing changed during the command.)
    manifest = tmp_path / "source.json"
    manifest.write_text(
        json.dumps({"artifact": {"sha256": "0" * 64}}), encoding="utf-8"
    )
    # WHEN adding a reaction with that manifest.
    # THEN it refuses before reading the model.
    with pytest.raises(ModelIntegrityError, match="recorded digest"):
        Cli().add_reaction(
            model=str(baseline),
            changeset=str(request_file),
            output=str(tmp_path / "candidate.xml"),
            source_manifest=str(manifest),
        )
    assert not (tmp_path / "candidate.xml").exists()


def test_payload_says_which_baseline_guarantee_was_given(
    baseline: Path, request_file: Path, tmp_path: Path
) -> None:
    # GIVEN a run with no manifest and no expected digest.
    payload = json.loads(
        Cli().add_reaction(
            model=str(baseline),
            changeset=str(request_file),
            output=str(tmp_path / "candidate.xml"),
        )
    )
    # WHEN reading the result.
    # THEN it states the weaker guarantee plainly, rather than letting "baseline_sha256"
    # be read as proof of provenance.
    assert "provenance unverified" in payload["baseline_verified_against"]

    manifest = tmp_path / "source.json"
    manifest.write_text(
        json.dumps({"artifact": {"sha256": file_digest(baseline)}}), encoding="utf-8"
    )
    verified = json.loads(
        Cli().add_reaction(
            model=str(baseline),
            changeset=str(request_file),
            output=str(tmp_path / "candidate2.xml"),
            source_manifest=str(manifest),
        )
    )
    assert "source manifest" in verified["baseline_verified_against"]


def test_export_refuses_to_publish_bytes_that_are_not_a_model(
    baseline: Path, request_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # GIVEN a writer that returns cleanly but produces something unreadable.
    # (Regression: export hashed the staged file without reading it back, so a file
    # containing "not SBML" was published with status "passed" -- existing on disk
    # and having a SHA-256 is not evidence of being a valid model.)
    candidate = tmp_path / "candidate.xml"
    Cli().add_reaction(
        model=str(baseline), changeset=str(request_file), output=str(candidate)
    )

    def garbage_writer(model: object, path: str) -> None:
        Path(path).write_text("not SBML", encoding="utf-8")

    monkeypatch.setattr(publish_module, "write_sbml_model", garbage_writer)
    delivered = tmp_path / "result.xml"
    # WHEN exporting.
    # THEN publication fails inside the taxonomy and nothing is left behind. The
    # category is model_integrity: the artifact is not what it claims to be, which is
    # a different problem from a candidate that failed its checks.
    with pytest.raises(ModelIntegrityError, match="could not be read"):
        Cli().export(
            model=str(baseline),
            candidate=str(candidate),
            changeset=str(request_file),
            output=str(delivered),
        )
    assert not delivered.exists()


def test_unreadable_candidate_is_a_structured_error(
    baseline: Path, request_file: Path, tmp_path: Path
) -> None:
    # GIVEN a candidate file the user supplied that is not SBML at all.
    # (Regression: load_model let COBRApy's CobraSBMLError escape, so an ordinary bad
    # input produced a traceback instead of a categorised error the agent can act on.)
    broken = tmp_path / "bad-candidate.xml"
    broken.write_text("not SBML", encoding="utf-8")
    # WHEN exporting from it.
    # THEN it arrives in the taxonomy naming the file.
    with pytest.raises(ModelIntegrityError) as caught:
        Cli().export(
            model=str(baseline),
            candidate=str(broken),
            changeset=str(request_file),
            output=str(tmp_path / "never.xml"),
        )
    assert caught.value.as_dict()["category"] == "model_integrity"
    assert not (tmp_path / "never.xml").exists()


def test_missing_model_file_is_a_structured_error(
    request_file: Path, tmp_path: Path
) -> None:
    # GIVEN a path that does not exist.
    # WHEN loading it.
    # THEN it is reported as an integrity problem, not a bare OSError.
    with pytest.raises(ModelIntegrityError, match="not found"):
        Cli().inspect(model=str(tmp_path / "absent.xml"))


def test_build_candidate_refuses_to_publish_unreadable_bytes(
    baseline: Path, request_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # GIVEN a serializer that returns cleanly having written something unusable.
    # (Regression: build_candidate hashed the staged file without reading it back, so
    # a Python caller following the documented API got a success payload and a digest
    # for a file that was not a model -- and that file is the input to every later
    # step.)
    def garbage_writer(model: object, path: str) -> None:
        Path(path).write_text("not SBML", encoding="utf-8")

    monkeypatch.setattr(publish_module, "write_sbml_model", garbage_writer)
    destination = tmp_path / "candidate.xml"
    spec = json.loads(request_file.read_text(encoding="utf-8"))
    # WHEN building a candidate.
    # THEN it fails and leaves nothing behind.
    with pytest.raises(ModelIntegrityError, match="could not be read"):
        build_candidate(
            baseline, ReactionRequest.from_dict(spec["operations"][0]), destination
        )
    assert not destination.exists()
    assert not list(tmp_path.glob(".*partial*"))


def test_export_refuses_to_publish_a_model_that_is_not_the_candidate(
    baseline: Path, request_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # GIVEN a writer that produces a perfectly valid SBML file which is not the
    # candidate that was checked -- here, the untouched baseline. Reading it back
    # succeeds, so only re-running the checks against the staged bytes catches it.
    candidate = tmp_path / "candidate.xml"
    Cli().add_reaction(
        model=str(baseline), changeset=str(request_file), output=str(candidate)
    )
    substitute = load_model(baseline)

    def writes_the_wrong_model(model: object, path: str) -> None:
        write_sbml_model(substitute, path)

    monkeypatch.setattr(publish_module, "write_sbml_model", writes_the_wrong_model)
    delivered = tmp_path / "result.xml"
    # WHEN exporting.
    # THEN publication is refused: the checked object and the published bytes must be
    # the same artifact, and hashing the file proves nothing about which model it is.
    with pytest.raises(ValidationFailedError, match="staged deliverable"):
        Cli().export(
            model=str(baseline),
            candidate=str(candidate),
            changeset=str(request_file),
            output=str(delivered),
        )
    assert not delivered.exists()


def test_syntactically_invalid_json_is_a_structured_error(
    baseline: Path, tmp_path: Path
) -> None:
    # GIVEN a reaction file that is not parsable JSON at all.
    # (Regression: only malformed *definitions* were converted; a syntax error still
    # escaped as a raw JSONDecodeError traceback, so the documented error taxonomy
    # did not hold for the most ordinary kind of hand-editing mistake.)
    broken = tmp_path / "broken.json"
    broken.write_text("{bad json", encoding="utf-8")
    # WHEN adding a reaction from it.
    # THEN it arrives in the taxonomy, naming the file and the position.
    with pytest.raises(RequestViolationError) as caught:
        Cli().add_reaction(
            model=str(baseline),
            changeset=str(broken),
            output=str(tmp_path / "never.xml"),
        )
    payload = caught.value.as_dict()
    assert payload["category"] == "request_violation"
    assert payload["line"] == 1
    assert not (tmp_path / "never.xml").exists()


def test_python_api_publishes_with_the_same_guarantees_as_the_cli(
    baseline: Path, request_file: Path, tmp_path: Path
) -> None:
    # GIVEN a Python caller using the library rather than the CLI.
    # (Regression: staging, source verification and re-checking lived inside the CLI
    # methods, so a Python caller could only reach the weaker save_candidate() path
    # and silently got none of the invariants the project advertises.)
    spec = json.loads(request_file.read_text(encoding="utf-8"))
    request = ReactionRequest.from_dict(spec["operations"][0])
    manifest = tmp_path / "source.json"
    manifest.write_text(
        json.dumps({"artifact": {"sha256": file_digest(baseline)}}), encoding="utf-8"
    )
    # WHEN building and publishing through the library API.
    built = build_candidate(
        baseline, request, tmp_path / "cand.xml", manifest=manifest
    )
    published = publish_deliverable(
        baseline,
        tmp_path / "cand.xml",
        request,
        tmp_path / "out.xml",
        manifest=manifest,
    )
    # THEN the same provenance and check guarantees appear in the result.
    assert "source manifest" in built.baseline_verified_against
    assert published.checks["status"] == "passed"
    assert (tmp_path / "out.xml").exists()


def test_export_reports_a_failing_candidate_as_distinct_from_a_damaged_baseline(
    baseline: Path, request_file: Path, tmp_path: Path
) -> None:
    # GIVEN a candidate that no longer matches the request, on an intact baseline.
    candidate = tmp_path / "candidate.xml"
    Cli().add_reaction(
        model=str(baseline), changeset=str(request_file), output=str(candidate)
    )
    tampered = json.loads(request_file.read_text(encoding="utf-8"))
    tampered["operations"][0]["metabolites"] = {"a_c": -2, "b_c": 1}
    request_file.write_text(json.dumps(tampered), encoding="utf-8")
    # WHEN exporting it.
    with pytest.raises(ValidationFailedError) as caught:
        Cli().export(
            model=str(baseline),
            candidate=str(candidate),
            changeset=str(request_file),
            output=str(tmp_path / "delivered.xml"),
        )
    # THEN the category says the candidate failed, not that the baseline is damaged.
    # The recoveries differ: regenerate the candidate versus abandon the run.
    assert caught.value.as_dict()["category"] == "validation_failed"


def test_export_delivers_a_passing_candidate(
    baseline: Path, request_file: Path, tmp_path: Path
) -> None:
    # GIVEN a candidate that still matches the request.
    candidate = tmp_path / "candidate.xml"
    Cli().add_reaction(
        model=str(baseline), changeset=str(request_file), output=str(candidate)
    )
    delivered = tmp_path / "delivered.xml"
    # WHEN exporting it.
    payload = json.loads(
        Cli().export(
            model=str(baseline),
            candidate=str(candidate),
            changeset=str(request_file),
            output=str(delivered),
        )
    )
    # THEN the deliverable exists and its digest is reported for the run record.
    assert delivered.exists()
    assert payload["status"] == "passed"
    assert payload["delivered_sha256"] == file_digest(delivered)
    # AND the consistency regression report is present and clean: this baseline
    # is small enough that MEMOTE genuinely runs against it (not mocked), so this
    # also proves the gate does not misfire on an ordinary passing change.
    assert payload["consistency_regression"]["ok"] is True
    # AND the payload's own scope says "export", not the "structural" value every
    # bare CheckResult reports -- this is the field that lets a caller distinguish
    # a `check` payload from an `export` payload without consulting documentation.
    assert payload["scope"] == "export"


def test_export_retains_memote_snapshots_when_regression_fails_without_rerunning(
    baseline: Path,
    request_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # GIVEN a completed review with a newly blocked reaction and a call counter.
    candidate = tmp_path / "candidate.xml"
    Cli().add_reaction(
        model=str(baseline), changeset=str(request_file), output=str(candidate)
    )
    before = ConsistencySnapshot(
        stoichiometrically_consistent=True,
        mass_unbalanced=frozenset(),
        charge_unbalanced=frozenset(),
        blocked_reactions=frozenset(),
        dead_end_metabolites=frozenset(),
        orphan_metabolites=frozenset(),
    )
    after = ConsistencySnapshot(
        stoichiometrically_consistent=True,
        mass_unbalanced=frozenset(),
        charge_unbalanced=frozenset(),
        blocked_reactions=frozenset({"FAKE_BLOCKED"}),
        dead_end_metabolites=frozenset(),
        orphan_metabolites=frozenset(),
    )
    regression = ConsistencyRegression(
        stoichiometric_consistency_lost=False,
        new_mass_unbalanced=(),
        new_charge_unbalanced=(),
        new_blocked_reactions=("FAKE_BLOCKED",),
        new_dead_end_metabolites=(),
        new_orphan_metabolites=(),
    )
    review = ConsistencyReview(before, after, regression)
    calls = 0

    def fake_review(
        before_model: cobra.Model, after_model: cobra.Model
    ) -> ConsistencyReview:
        nonlocal calls
        calls += 1
        return review

    monkeypatch.setattr(publish_module, "review_consistency", fake_review)
    record_directory = tmp_path / "record"
    record_directory.mkdir()
    delivered = record_directory / "result.xml"
    # WHEN exporting the candidate.
    with pytest.raises(ValidationFailedError, match="consistency regression"):
        Cli().export(
            model=str(baseline),
            candidate=str(candidate),
            changeset=str(request_file),
            output=str(delivered),
            record_directory=str(record_directory),
        )
    # THEN one review supplies both durable snapshots and the failure summary.
    assert calls == 1
    assert json.loads(
        (record_directory / "memote_before.json").read_text(encoding="utf-8")
    ) == before.as_dict()
    assert json.loads(
        (record_directory / "memote_after.json").read_text(encoding="utf-8")
    ) == after.as_dict()
    summary = json.loads(
        (record_directory / "validation_summary.json").read_text(encoding="utf-8")
    )
    assert summary["consistency_regression"] == regression.as_dict()
    assert summary["status"] == "failed"
    assert summary["scope"] == "export"
    assert summary["failed"] == ["consistency regression"]
    assert not delivered.exists()


def test_export_records_snapshots_and_summary_without_expanding_the_payload(
    baseline: Path, request_file: Path, tmp_path: Path
) -> None:
    # GIVEN a candidate that passes local and whole-model validation.
    candidate = tmp_path / "candidate.xml"
    Cli().add_reaction(
        model=str(baseline), changeset=str(request_file), output=str(candidate)
    )
    record_directory = tmp_path / "record"
    record_directory.mkdir()
    delivered = record_directory / "result.xml"
    # WHEN exporting with the optional package-owned recording destination.
    payload = json.loads(
        Cli().export(
            model=str(baseline),
            candidate=str(candidate),
            changeset=str(request_file),
            output=str(delivered),
            record_directory=str(record_directory),
        )
    )
    # THEN result.xml and all three export artifacts are complete.
    assert delivered.exists()
    before = json.loads(
        (record_directory / "memote_before.json").read_text(encoding="utf-8")
    )
    after = json.loads(
        (record_directory / "memote_after.json").read_text(encoding="utf-8")
    )
    summary = json.loads(
        (record_directory / "validation_summary.json").read_text(encoding="utf-8")
    )
    assert before["stoichiometrically_consistent"] is True
    assert after["stoichiometrically_consistent"] is True
    assert summary["status"] == "passed"
    assert summary["reaction_id"] == "NEWRXN"
    assert "baseline_verified_against" in summary
    assert summary["consistency_regression"] == payload["consistency_regression"]
    # AND ordinary ExportResult JSON remains the compatibility payload.
    assert "memote_before" not in payload
    assert "memote_after" not in payload
    # AND Hermes-owned session files remain the orchestrator's responsibility.
    assert not (record_directory / "request.md").exists()
    assert not (record_directory / "summary.md").exists()
    assert not (record_directory / "run_status.json").exists()


def test_export_preflights_all_record_paths_before_running_memote(
    baseline: Path,
    request_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # GIVEN a valid candidate and one pre-existing export artifact.
    candidate = tmp_path / "candidate.xml"
    Cli().add_reaction(
        model=str(baseline), changeset=str(request_file), output=str(candidate)
    )
    record_directory = tmp_path / "record"
    record_directory.mkdir()
    existing = record_directory / "memote_after.json"
    existing.write_text("prior evidence", encoding="utf-8")
    calls = 0

    def counted_review(
        before_model: cobra.Model, after_model: cobra.Model
    ) -> ConsistencyReview:
        nonlocal calls
        calls += 1
        return review_consistency(before_model, after_model)

    monkeypatch.setattr(publish_module, "review_consistency", counted_review)
    # WHEN export is asked to write that artifact set.
    with pytest.raises(ModelIntegrityError, match="already exists"):
        Cli().export(
            model=str(baseline),
            candidate=str(candidate),
            changeset=str(request_file),
            output=str(record_directory / "result.xml"),
            record_directory=str(record_directory),
        )
    # THEN no MEMOTE work runs and no new sibling artifact is published.
    assert calls == 0
    assert not (record_directory / "memote_before.json").exists()
    assert not (record_directory / "validation_summary.json").exists()
    assert existing.read_text(encoding="utf-8") == "prior evidence"


def test_export_refuses_to_deliver_a_candidate_with_a_consistency_regression(
    baseline: Path, request_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # GIVEN a candidate that passes every other check, but a consistency review that
    # reports a regression. Reproducing a genuine MEMOTE-detectable regression
    # through add_reaction alone would need a model complex enough to expose real
    # network gaps; the gate's own logic (identifying an actual regression) is
    # already covered directly in test_consistency_review.py. What is not covered
    # anywhere else is whether publish_deliverable actually wires that verdict into
    # a refusal -- a claimed gate with no test exercising its "no" path is not a
    # verified gate, so this supplies a completed review at the orchestration seam.
    candidate = tmp_path / "candidate.xml"
    Cli().add_reaction(
        model=str(baseline), changeset=str(request_file), output=str(candidate)
    )
    fake_regression = ConsistencyRegression(
        stoichiometric_consistency_lost=False,
        new_mass_unbalanced=(),
        new_charge_unbalanced=(),
        new_blocked_reactions=("FAKE_BLOCKED",),
        new_dead_end_metabolites=(),
        new_orphan_metabolites=(),
    )
    before = ConsistencySnapshot(
        stoichiometrically_consistent=True,
        mass_unbalanced=frozenset(),
        charge_unbalanced=frozenset(),
        blocked_reactions=frozenset(),
        dead_end_metabolites=frozenset(),
        orphan_metabolites=frozenset(),
    )
    after = ConsistencySnapshot(
        stoichiometrically_consistent=True,
        mass_unbalanced=frozenset(),
        charge_unbalanced=frozenset(),
        blocked_reactions=frozenset({"FAKE_BLOCKED"}),
        dead_end_metabolites=frozenset(),
        orphan_metabolites=frozenset(),
    )
    fake_review = ConsistencyReview(before, after, fake_regression)
    monkeypatch.setattr(
        publish_module,
        "review_consistency",
        lambda before_model, after_model: fake_review,
    )
    delivered = tmp_path / "delivered.xml"
    # WHEN exporting it.
    # THEN delivery is refused, nothing is published, and the regression detail is
    # attached to the raised error for the caller to act on.
    with pytest.raises(ValidationFailedError, match="consistency regression") as caught:
        Cli().export(
            model=str(baseline),
            candidate=str(candidate),
            changeset=str(request_file),
            output=str(delivered),
        )
    assert not delivered.exists()
    assert caught.value.as_dict()["category"] == "validation_failed"
    assert "FAKE_BLOCKED" in caught.value.as_dict()["consistency_regression"][
        "new_blocked_reactions"
    ]
    assert not list(tmp_path.glob(".*.partial"))
