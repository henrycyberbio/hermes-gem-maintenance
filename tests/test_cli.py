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
from hermes_gem_maintenance.errors import (
    InsufficientInformationError,
    ModelIntegrityError,
    RequestViolationError,
    ValidationFailedError,
)
from hermes_gem_maintenance.inspect import require_unique_metabolite
from hermes_gem_maintenance.model_io import file_digest, load_model
from hermes_gem_maintenance.publish import build_candidate, publish_deliverable

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
        reaction=str(request_file),
        output=str(candidate),
    )
    delivered = tmp_path / "delivered.xml"
    # WHEN exporting it.
    # THEN delivery is refused: an untested model must not ship as a verified one.
    with pytest.raises(ValidationFailedError, match="could not be fully verified"):
        Cli().export(
            model=str(unverifiable_baseline),
            candidate=str(candidate),
            reaction=str(request_file),
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
        reaction=str(request_file),
        output=str(candidate),
    )
    # WHEN checking it.
    payload = json.loads(
        Cli().check(
            model=str(unverifiable_baseline),
            candidate=str(candidate),
            reaction=str(request_file),
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
    malformed["metabolites"] = [["a_c", -1], ["b_c", 1]]
    request_file.write_text(json.dumps(malformed), encoding="utf-8")
    # WHEN adding the reaction.
    # THEN it fails inside the taxonomy, carrying a category the agent can act on.
    with pytest.raises(RequestViolationError) as caught:
        Cli().add_reaction(
            model=str(baseline),
            reaction=str(request_file),
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
        model=str(baseline), reaction=str(request_file), output=str(candidate)
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
            reaction=str(request_file),
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
            reaction=str(request_file),
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
            reaction=str(request_file),
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
            reaction=str(request_file),
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
        model=str(baseline), reaction=str(request_file), output=str(candidate)
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
            reaction=str(request_file),
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
            reaction=str(request_file),
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
        build_candidate(baseline, ReactionRequest.from_dict(spec), destination)
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
        model=str(baseline), reaction=str(request_file), output=str(candidate)
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
            reaction=str(request_file),
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
            reaction=str(broken),
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
    # (Regression: a library caller reached only save_candidate(), which performs no
    # staging, source verification or re-checking.)
    spec = json.loads(request_file.read_text(encoding="utf-8"))
    request = ReactionRequest.from_dict(spec)
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


def test_export_refuses_a_candidate_that_changed_since_it_was_built(
    baseline: Path, request_file: Path, tmp_path: Path
) -> None:
    # GIVEN a candidate whose bytes were replaced after its digest was recorded.
    # (Regression: the baseline was pinned by digest but the candidate was not, so
    # the file could be swapped between building it and publishing it.)
    request = ReactionRequest.from_dict(
        json.loads(request_file.read_text(encoding="utf-8"))
    )
    built = build_candidate(baseline, request, tmp_path / "cand.xml")
    (tmp_path / "cand.xml").write_text("not a model", encoding="utf-8")
    # WHEN exporting it against the digest that was reported at build time.
    # THEN it is refused as an integrity failure and nothing is delivered.
    with pytest.raises(ModelIntegrityError):
        publish_deliverable(
            baseline,
            tmp_path / "cand.xml",
            request,
            tmp_path / "out.xml",
            candidate_sha256=built.candidate_sha256,
        )
    assert not (tmp_path / "out.xml").exists()


def test_export_parses_the_candidate_once(
    baseline: Path, request_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # GIVEN a candidate built from the baseline.
    # (Regression: the candidate was read three times, so the checked bytes, the
    # written bytes and the compared bytes could each come from a different file.)
    request = ReactionRequest.from_dict(
        json.loads(request_file.read_text(encoding="utf-8"))
    )
    built = build_candidate(baseline, request, tmp_path / "cand.xml")
    reads: list[str] = []
    original = publish_module.load_model

    def spy(path: Path) -> cobra.Model:
        reads.append(Path(path).name)
        return original(path)

    monkeypatch.setattr(publish_module, "load_model", spy)
    # WHEN exporting it.
    publish_deliverable(
        baseline,
        built.candidate,
        request,
        tmp_path / "out.xml",
        candidate_sha256=built.candidate_sha256,
    )
    # THEN the candidate is parsed exactly once, and so is each other artifact.
    assert reads.count("cand.xml") == 1
    assert len(reads) == 3


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
