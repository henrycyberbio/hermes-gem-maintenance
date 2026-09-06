"""Behaviour checks for the command-line interface.

These exercise the commands an agent actually calls, including the contract that
matters most: a failing candidate must not produce a deliverable.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import cobra
import pytest
from cobra.io import write_sbml_model

from hermes_gem_maintenance.cli import Cli
from hermes_gem_maintenance.errors import (
    InsufficientInformationError,
    ModelIntegrityError,
    RequestViolationError,
    ValidationFailedError,
)
from hermes_gem_maintenance.inspect import require_unique_metabolite
from hermes_gem_maintenance.model_io import file_digest, load_model

if TYPE_CHECKING:
    from pathlib import Path

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
    path = tmp_path / "baseline.xml"
    write_sbml_model(model, str(path))
    return path


@pytest.fixture
def request_file(tmp_path: Path) -> Path:
    """A balanced, unambiguous reaction request."""
    path = tmp_path / "reaction.json"
    path.write_text(
        json.dumps(
            {
                "reaction_id": "NEWRXN",
                "metabolites": {"a_c": -1, "b_c": 1},
                "lower_bound": 0.0,
                "upper_bound": 1000.0,
                "gene_reaction_rule": "geneA",
            }
        ),
        encoding="utf-8",
    )
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
            model=str(baseline), reaction=str(request_file), output=str(output)
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
            model=str(baseline), reaction=str(request_file), output=str(baseline)
        )
    assert file_digest(baseline) == before


def test_add_reaction_distinguishes_a_violation_from_missing_information(
    baseline: Path, tmp_path: Path
) -> None:
    # GIVEN one request that conflicts with the model and one that is incomplete.
    conflict = tmp_path / "conflict.json"
    conflict.write_text(
        json.dumps(
            {
                "reaction_id": "EXIST",
                "metabolites": {"a_c": -1, "b_c": 1},
                "lower_bound": 0.0,
                "upper_bound": 1000.0,
            }
        ),
        encoding="utf-8",
    )
    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text(
        json.dumps({"reaction_id": "NEWRXN", "metabolites": {"a_c": -1}}),
        encoding="utf-8",
    )
    # WHEN submitting each.
    # THEN the categories differ: one needs a different request, one needs more facts.
    with pytest.raises(RequestViolationError):
        Cli().add_reaction(
            model=str(baseline),
            reaction=str(conflict),
            output=str(tmp_path / "a.xml"),
        )
    with pytest.raises(InsufficientInformationError):
        Cli().add_reaction(
            model=str(baseline),
            reaction=str(incomplete),
            output=str(tmp_path / "b.xml"),
        )


# ==== check and export ====


def test_check_passes_a_candidate_produced_by_add_reaction(
    baseline: Path, request_file: Path, tmp_path: Path
) -> None:
    # GIVEN a candidate written by the add command and reloaded from disk.
    candidate = tmp_path / "candidate.xml"
    Cli().add_reaction(
        model=str(baseline), reaction=str(request_file), output=str(candidate)
    )
    # WHEN checking it.
    payload = json.loads(
        Cli().check(
            model=str(baseline),
            candidate=str(candidate),
            reaction=str(request_file),
        )
    )
    # THEN it passes; the roundtrip through SBML must not invalidate a good candidate.
    assert payload["status"] == "passed"
    assert payload["failed"] == []


def test_export_refuses_to_deliver_a_failing_candidate(
    baseline: Path, request_file: Path, tmp_path: Path
) -> None:
    # GIVEN a candidate that no longer matches the request.
    candidate = tmp_path / "candidate.xml"
    Cli().add_reaction(
        model=str(baseline), reaction=str(request_file), output=str(candidate)
    )
    tampered = json.loads(request_file.read_text(encoding="utf-8"))
    tampered["metabolites"] = {"a_c": -2, "b_c": 1}
    request_file.write_text(json.dumps(tampered), encoding="utf-8")
    delivered = tmp_path / "delivered.xml"
    # WHEN exporting it.
    # THEN delivery is refused and no file is written; a failed check must not ship.
    with pytest.raises(ValidationFailedError, match="re-validation"):
        Cli().export(
            model=str(baseline),
            candidate=str(candidate),
            reaction=str(request_file),
            output=str(delivered),
        )
    assert not delivered.exists()


def test_export_reports_a_failing_candidate_as_distinct_from_a_damaged_baseline(
    baseline: Path, request_file: Path, tmp_path: Path
) -> None:
    # GIVEN a candidate that no longer matches the request, on an intact baseline.
    candidate = tmp_path / "candidate.xml"
    Cli().add_reaction(
        model=str(baseline), reaction=str(request_file), output=str(candidate)
    )
    tampered = json.loads(request_file.read_text(encoding="utf-8"))
    tampered["metabolites"] = {"a_c": -2, "b_c": 1}
    request_file.write_text(json.dumps(tampered), encoding="utf-8")
    # WHEN exporting it.
    with pytest.raises(ValidationFailedError) as caught:
        Cli().export(
            model=str(baseline),
            candidate=str(candidate),
            reaction=str(request_file),
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
        model=str(baseline), reaction=str(request_file), output=str(candidate)
    )
    delivered = tmp_path / "delivered.xml"
    # WHEN exporting it.
    payload = json.loads(
        Cli().export(
            model=str(baseline),
            candidate=str(candidate),
            reaction=str(request_file),
            output=str(delivered),
        )
    )
    # THEN the deliverable exists and its digest is reported for the run record.
    assert delivered.exists()
    assert payload["status"] == "passed"
    assert payload["delivered_sha256"] == file_digest(delivered)
