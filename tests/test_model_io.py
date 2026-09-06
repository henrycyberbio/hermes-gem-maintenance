"""Behaviour checks for model IO guards and metabolite resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

import cobra
import pytest

from hermes_gem_maintenance import (
    describe_metabolite,
    describe_reaction,
    file_digest,
    require_unique_metabolite,
    resolve_metabolite,
    save_candidate,
    summarize,
    verify_digest,
)
from hermes_gem_maintenance.errors import (
    InsufficientInformationError,
    ModelIntegrityError,
)
from hermes_gem_maintenance.inspect import WEAK_MATCH_CEILING

if TYPE_CHECKING:
    from pathlib import Path

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
