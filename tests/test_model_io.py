"""Behaviour checks for model IO guards and metabolite resolution."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import cobra
import pytest

from hermes_gem_maintenance import (
    describe_metabolite,
    describe_reaction,
    file_digest,
    require_unique_metabolite,
    resolve_metabolite,
    staged_write,
    summarize,
    verify_digest,
)
from hermes_gem_maintenance import model_io as io_module
from hermes_gem_maintenance.errors import (
    InsufficientInformationError,
    ModelIntegrityError,
)
from hermes_gem_maintenance.inspect import WEAK_MATCH_CEILING
from hermes_gem_maintenance.model_io import save_candidate

# ==== fixtures ====


@pytest.fixture
def model() -> cobra.Model:
    """Model with a name collision across compartments and substring-colliding ids."""
    m = cobra.Model("toy")
    m.compartments = {"c": "cytosol", "p": "periplasm"}
    # ppi_c contains pi_c: a real BiGG pairing that makes substring noise unavoidable.
    specs = [
        ("f6p_c", "c", "D-Fructose 6-phosphate", "C6H11O9P"),
        ("f6p_p", "p", "D-Fructose 6-phosphate", "C6H11O9P"),
        ("pi_c", "c", "Phosphate", "HO4P"),
        ("ppi_c", "c", "Diphosphate", "HO7P2"),
    ]
    for mid, compartment, name, formula in specs:
        m.add_metabolites(
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
    reaction = cobra.Reaction("R1", lower_bound=0.0, upper_bound=1000.0)
    m.add_reactions([reaction])
    reaction.add_metabolites({m.metabolites.f6p_c: -1, m.metabolites.pi_c: 1})
    return m


# ==== summaries ====


def test_summarize_reports_counts_and_compartments(model: cobra.Model) -> None:
    # GIVEN a model holding one reaction and four metabolites.
    # WHEN summarizing it.
    summary = summarize(model)
    # THEN the caller gets the facts needed before proposing a change. The counts are
    # stated outright: comparing against len(model...) would restate the
    # implementation and assert nothing.
    assert summary["reactions"] == 1
    assert summary["metabolites"] == 4
    assert summary["compartments"] == {"c": "cytosol", "p": "periplasm"}


def test_describe_returns_none_for_absent_objects(model: cobra.Model) -> None:
    # GIVEN identifiers the model does not contain.
    # WHEN describing them.
    # THEN None is returned rather than an exception; absence is a normal answer.
    assert describe_reaction(model, "NOPE") is None
    assert describe_metabolite(model, "nope_c") is None
    assert describe_reaction(model, "R1") is not None


# ==== resolution ====


def test_resolve_returns_every_candidate_for_an_ambiguous_name(
    model: cobra.Model,
) -> None:
    # GIVEN a name shared by metabolites in two compartments.
    # WHEN resolving it.
    candidates = resolve_metabolite(model, "D-Fructose 6-phosphate")
    # THEN both are returned; the command does not pick one.
    assert {c["id"] for c in candidates} == {"f6p_c", "f6p_p"}


def test_resolve_narrows_by_compartment(model: cobra.Model) -> None:
    # GIVEN the same ambiguous name plus a compartment.
    # WHEN resolving within that compartment.
    candidates = resolve_metabolite(model, "D-Fructose 6-phosphate", "p")
    # THEN only the matching compartment is returned.
    assert [c["id"] for c in candidates] == ["f6p_p"]


def test_resolve_ranks_an_exact_identifier_first(model: cobra.Model) -> None:
    # GIVEN a query that is an exact identifier and also a substring of another.
    # WHEN resolving it.
    candidates = resolve_metabolite(model, "f6p_c")
    # THEN the exact match leads; substring matches must not outrank it.
    assert candidates[0]["id"] == "f6p_c"
    assert candidates[0]["matched_on"] == "exact identifier"


def test_require_unique_raises_with_the_candidates_listed(model: cobra.Model) -> None:
    # GIVEN an ambiguous name.
    # WHEN demanding a unique match.
    # THEN it raises as insufficient information and names the candidates, so the
    # caller can ask a specific question instead of guessing.
    with pytest.raises(InsufficientInformationError) as caught:
        require_unique_metabolite(model, "D-Fructose 6-phosphate")
    listed = {c["id"] for c in caught.value.context["candidates"]}
    assert listed == {"f6p_c", "f6p_p"}


def test_require_unique_accepts_an_unambiguous_identifier(model: cobra.Model) -> None:
    # GIVEN an exact identifier.
    # WHEN demanding a unique match.
    # THEN it resolves without complaint.
    assert require_unique_metabolite(model, "pi_c")["id"] == "pi_c"


def test_require_unique_refuses_a_name_shared_across_compartments(
    model: cobra.Model,
) -> None:
    # GIVEN a name carried by metabolites in two compartments, both exact matches.
    # WHEN demanding a unique match.
    # THEN it refuses: equally strong matches must never be silently ranked, because
    # picking a compartment for the requester is inventing biology.
    with pytest.raises(InsufficientInformationError) as caught:
        require_unique_metabolite(model, "D-Fructose 6-phosphate")
    compartments = {c["compartment"] for c in caught.value.context["candidates"]}
    assert compartments == {"c", "p"}


def test_require_unique_ignores_substring_noise_around_an_exact_identifier(
    model: cobra.Model,
) -> None:
    # GIVEN pi_c, whose identifier is a substring of ppi_c.
    # WHEN demanding a unique match.
    # THEN the exact hit wins: a weaker match kind is noise, not ambiguity, and
    # treating it as ambiguity would block every query with a shorter identifier.
    assert require_unique_metabolite(model, "pi_c")["id"] == "pi_c"


def test_resolve_reaches_a_metabolite_by_formula(model: cobra.Model) -> None:
    # GIVEN a metabolite whose name no natural query would find. BiGG stores water
    # as "H2O H2O", so name matching cannot reach it.
    model.add_metabolites(
        [cobra.Metabolite("h2o_c", name="H2O H2O", formula="H2O", charge=0,
                          compartment="c")]
    )
    # WHEN resolving by formula.
    match = require_unique_metabolite(model, "H2O")
    # THEN it resolves. Without this the caller must guess an identifier, which is
    # exactly what the workflow forbids.
    assert match["id"] == "h2o_c"
    assert match["matched_on"] == "exact formula"


def test_resolve_refuses_a_vague_query_despite_one_exact_hit(
    model: cobra.Model,
) -> None:
    # GIVEN a word that names one metabolite exactly and appears in many others.
    for index in range(WEAK_MATCH_CEILING + 2):
        model.add_metabolites(
            [cobra.Metabolite(f"x{index}_c", name=f"Phosphate carrier {index}",
                              formula="HO4P", charge=0, compartment="c")]
        )
    # WHEN demanding a unique match for the bare word.
    # THEN it refuses. One exact hit inside a crowd of 14 is not identification, and
    # answering confidently would hand the caller a mapping they never asked for.
    with pytest.raises(InsufficientInformationError):
        require_unique_metabolite(model, "Phosphate")


def test_exact_identifier_outranks_a_crowd_of_substring_matches(
    model: cobra.Model,
) -> None:
    # GIVEN an identifier that also appears inside far more identifiers than the
    # weak-match ceiling allows. (Regression: the crowd test ran before the exact
    # check, so six real identifiers in the frozen model -- g3p_c among them, a
    # participant of a documented example -- could not be resolved at all.)
    for index in range(WEAK_MATCH_CEILING + 5):
        model.add_metabolites(
            [cobra.Metabolite(f"pi_c_variant{index}", name=f"Carrier {index}",
                              formula="HO4P", charge=0, compartment="c")]
        )
    # WHEN demanding a unique match for the exact identifier.
    # THEN it resolves: an identifier is unique within a model by construction, so
    # substring noise cannot make it ambiguous.
    assert require_unique_metabolite(model, "pi_c")["id"] == "pi_c"


def test_shared_formula_across_compartments_stays_ambiguous(
    model: cobra.Model,
) -> None:
    # GIVEN the same species present in three compartments, as water is in a real
    # model. (The documentation claimed `H2O` resolved outright; it cannot, because
    # h2o_c, h2o_e and h2o_p all match the formula exactly.)
    for compartment in ("c", "e", "p"):
        model.add_metabolites(
            [cobra.Metabolite(f"h2o_{compartment}", name="H2O H2O", formula="H2O",
                              charge=0, compartment=compartment)]
        )
    # WHEN resolving by formula without naming a compartment.
    # THEN it refuses; narrowing by compartment is the caller's decision.
    with pytest.raises(InsufficientInformationError):
        require_unique_metabolite(model, "H2O")
    assert require_unique_metabolite(model, "H2O", compartment="c")["id"] == "h2o_c"


def test_case_matters_for_formula_and_identifier_matching(
    model: cobra.Model,
) -> None:
    # GIVEN two metabolites whose formulae differ only in case: CO is carbon
    # monoxide, Co is cobalt. (Regression: query, id, name and formula were all
    # lowercased before matching, so `CO` was reported as an *exact formula* match
    # for cobalt -- a chemistry error dressed up as a search result.)
    model.add_metabolites([
        cobra.Metabolite("co_c", name="Carbon monoxide", formula="CO", charge=0,
                         compartment="c"),
        cobra.Metabolite("cobalt2_c", name="Cobalt", formula="Co", charge=2,
                         compartment="c"),
    ])
    # WHEN resolving each formula exactly.
    carbon = require_unique_metabolite(model, "CO")
    cobalt = require_unique_metabolite(model, "Co")
    # THEN they resolve to different metabolites.
    assert carbon["id"] == "co_c"
    assert cobalt["id"] == "cobalt2_c"


def test_identifier_case_is_not_folded(model: cobra.Model) -> None:
    # GIVEN a metabolite whose identifier carries meaningful capitals.
    model.add_metabolites(
        [cobra.Metabolite("ACP_c", name="Acyl carrier protein", formula="C11H21N2O7PRS",
                          charge=0, compartment="c")]
    )
    # WHEN querying with the wrong case.
    candidates = resolve_metabolite(model, "acp_c")
    # THEN it is not an exact identifier match; identifiers are exact tokens.
    assert not any(c["matched_on"] == "exact identifier" for c in candidates)
    assert require_unique_metabolite(model, "ACP_c")["id"] == "ACP_c"


def test_names_remain_case_insensitive(model: cobra.Model) -> None:
    # GIVEN a metabolite with a natural-language name.
    model.add_metabolites(
        [cobra.Metabolite("zzz_c", name="Peculiar Compound", formula="C9H9",
                          charge=0, compartment="c")]
    )
    # WHEN querying it in a different case.
    # THEN it still matches: prose carries no case convention, unlike formulae.
    assert require_unique_metabolite(model, "peculiar compound")["id"] == "zzz_c"


def test_publication_refuses_a_destination_created_after_the_guard(
    tmp_path: Path,
) -> None:
    # GIVEN a run that is beaten to its output path by another writer after the
    # up-front existence check. (Regression: publication used Path.replace(), which
    # overwrites, so the documented refusal to clobber evidence was not enforced.)
    protected = tmp_path / "base.xml"
    protected.write_text("baseline", encoding="utf-8")
    destination = tmp_path / "result.xml"
    # WHEN publishing over the file the other writer left.
    # THEN it refuses, and the other writer's bytes survive untouched.
    def race() -> None:
        with staged_write(destination, protected=protected) as staged:
            staged.write_text("mine", encoding="utf-8")
            destination.write_text("late evidence", encoding="utf-8")

    with pytest.raises(ModelIntegrityError, match="already exists"):
        race()
    assert destination.read_text(encoding="utf-8") == "late evidence"


def test_staging_does_not_disturb_another_run(tmp_path: Path) -> None:
    # GIVEN a concurrent run's staging file sitting in the output directory.
    # (Regression: the staging name was a fixed `.<name>.partial` that was unlinked
    # on entry, destroying whatever another run had in progress.)
    protected = tmp_path / "base.xml"
    protected.write_text("baseline", encoding="utf-8")
    destination = tmp_path / "result.xml"
    other = tmp_path / ".result.xml.partial"
    other.write_text("another run's work", encoding="utf-8")
    # WHEN a new run stages and publishes.
    with staged_write(destination, protected=protected) as staged:
        staged.write_text("mine", encoding="utf-8")
    # THEN the other run's file is untouched.
    assert other.read_text(encoding="utf-8") == "another run's work"
    assert destination.read_text(encoding="utf-8") == "mine"


def test_staging_never_deletes_the_protected_baseline(tmp_path: Path) -> None:
    # GIVEN a baseline whose name collides with the old fixed staging pattern.
    # (Regression: `.out.xml.partial` as a baseline was unlinked by the staging
    # cleanup -- the guard deleted the very file it existed to protect.)
    protected = tmp_path / ".out.xml.partial"
    protected.write_text("PRECIOUS BASELINE", encoding="utf-8")
    destination = tmp_path / "out.xml"
    # WHEN a run stages and publishes.
    with staged_write(destination, protected=protected) as staged:
        staged.write_text("mine", encoding="utf-8")
    # THEN the baseline is intact.
    assert protected.read_text(encoding="utf-8") == "PRECIOUS BASELINE"


def test_failed_run_leaves_no_staging_file(tmp_path: Path) -> None:
    # GIVEN a run that raises after writing its staged bytes.
    protected = tmp_path / "base.xml"
    protected.write_text("baseline", encoding="utf-8")
    destination = tmp_path / "result.xml"
    # WHEN the body fails.
    def failing_run() -> None:
        with staged_write(destination, protected=protected) as staged:
            staged.write_text("partial work", encoding="utf-8")
            msg = "check failed"
            raise RuntimeError(msg)

    with pytest.raises(RuntimeError):
        failing_run()
    # THEN neither the destination nor a partial file remains.
    assert not destination.exists()
    assert not list(tmp_path.glob(".*partial*"))


def test_fallback_publication_leaves_nothing_when_the_copy_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # GIVEN a filesystem with no hard links, where publication must create the
    # destination before copying into it, and a copy that fails partway.
    # (Regression: the exclusive-create fallback left a zero-length file at the final
    # path when the copy raised -- a visible artifact where a failed run promises
    # none.)
    protected = tmp_path / "base.xml"
    protected.write_text("baseline", encoding="utf-8")
    destination = tmp_path / "fallback.xml"

    def no_hard_links(src: object, dst: object) -> None:
        msg = "no hard links here"
        raise OSError(msg)

    monkeypatch.setattr(io_module.os, "link", no_hard_links)
    original_read = Path.read_bytes

    def failing_read(self: Path) -> bytes:
        if self.name.endswith(".partial"):
            msg = "read failed mid-copy"
            raise OSError(msg)
        return original_read(self)

    monkeypatch.setattr(Path, "read_bytes", failing_read)

    def publish_run() -> None:
        with staged_write(destination, protected=protected) as staged:
            staged.write_text("candidate bytes", encoding="utf-8")

    # WHEN publishing.
    with pytest.raises(OSError, match="mid-copy"):
        publish_run()
    # THEN no truncated deliverable is left at the destination.
    assert not destination.exists()


# ==== write guards ====


def test_save_candidate_refuses_to_overwrite_the_baseline(
    model: cobra.Model, tmp_path: Path
) -> None:
    # GIVEN a baseline model file.
    baseline = tmp_path / "baseline.xml"
    save_candidate(model, baseline, protected=tmp_path / "other.xml")
    digest = file_digest(baseline)
    # WHEN writing a candidate to that same path.
    # THEN it refuses, and the baseline bytes are untouched.
    with pytest.raises(ModelIntegrityError, match="baseline"):
        save_candidate(model, baseline, protected=baseline)
    assert file_digest(baseline) == digest


def test_save_candidate_refuses_an_existing_path(
    model: cobra.Model, tmp_path: Path
) -> None:
    # GIVEN a path that already holds a file.
    existing = tmp_path / "candidate.xml"
    existing.write_text("prior", encoding="utf-8")
    # WHEN writing a candidate there.
    # THEN it refuses rather than silently replacing prior evidence.
    with pytest.raises(ModelIntegrityError, match="already exists"):
        save_candidate(model, existing, protected=tmp_path / "baseline.xml")
    assert existing.read_text(encoding="utf-8") == "prior"


def test_save_candidate_detects_the_baseline_through_a_relative_path(
    model: cobra.Model, tmp_path: Path
) -> None:
    # GIVEN a baseline referenced by an unnormalized path.
    baseline = tmp_path / "baseline.xml"
    save_candidate(model, baseline, protected=tmp_path / "other.xml")
    indirect = tmp_path / "sub" / ".." / "baseline.xml"
    # WHEN writing a candidate to the same file by that path.
    # THEN the guard still fires; it compares resolved paths, not strings.
    with pytest.raises(ModelIntegrityError, match="baseline"):
        save_candidate(model, indirect, protected=baseline)


# ==== digests ====


def test_verify_digest_reports_the_mismatch(tmp_path: Path) -> None:
    # GIVEN a file whose contents changed after its digest was recorded.
    path = tmp_path / "model.xml"
    path.write_text("original", encoding="utf-8")
    recorded = file_digest(path)
    path.write_text("tampered", encoding="utf-8")
    # WHEN verifying against the recorded digest.
    # THEN it raises and reports both digests, so the drift is diagnosable.
    with pytest.raises(ModelIntegrityError) as caught:
        verify_digest(path, recorded)
    assert caught.value.context["expected"] == recorded
    assert caught.value.context["actual"] != recorded
