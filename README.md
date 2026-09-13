# Hermes-GEM Maintenance

Maintaining a genome-scale metabolic model (GEM) is manual work: published findings
have to be turned into concrete reactions, mapped onto the identifiers the model
already uses, and checked before the change is accepted.

This project lets Hermes carry out one such task end-to-end. Given a public model, a
request in plain language, and the supporting evidence, Hermes reads the model,
resolves the metabolites, constructs the reaction, applies it to a working copy,
checks the result, and reports what changed.

A Python package does what can be decided deterministically — reading models, listing
candidates, applying a reaction, checking the outcome. A Hermes skill covers what
cannot: interpreting the request, judging whether a mapping is unambiguous, deciding
when to ask rather than assume. Source models are immutable; every operation reads
the original or writes an independent copy.

**Status:** the package and CLI are working and tested against the frozen model. The
skill in `skills/gem-maintenance/` drives them from a natural-language request; see
[`examples/`](examples/) for the worked case and for requests the workflow refuses.

## Use

```bash
uv sync
```

### The worked example

```bash
uv run python scripts/add_reaction_walkthrough.py run
```

Adds reaction `PKETF` to the frozen `iEC1372_W3110` model and writes `candidate.xml`
plus the check artifacts to `runs/PKETF/`. Ten checks run: reaction absent from
the baseline and present in the candidate, stoichiometry, bounds, gene rule, name,
subsystem, elemental and charge conservation, FBA feasibility, no unrelated semantic
changes. Candidate construction separately verifies the source digest and SBML
roundtrip before publishing `candidate.xml`.

### The commands

Each subcommand prints JSON and exits non-zero on a deliberate error.

```bash
uv run hermes-gem-maintenance inspect  --model=MODEL [--reaction=ID | --metabolite=ID]
uv run hermes-gem-maintenance resolve  --model=MODEL --query=NAME [--compartment=C]
uv run hermes-gem-maintenance add_reaction --model=MODEL --changeset=CHANGESET.json --output=CAND.xml [--source_manifest=SRC.json]
uv run hermes-gem-maintenance delete_reaction --model=MODEL --changeset=CHANGESET.json --output=CAND.xml [--source_manifest=SRC.json]
uv run hermes-gem-maintenance check    --model=MODEL --candidate=CAND.xml --changeset=CHANGESET.json [--record_directory=RUN-DIR]
uv run hermes-gem-maintenance export   --model=MODEL --candidate=CAND.xml --changeset=CHANGESET.json --output=OUT.xml [--candidate_sha256=DIGEST] [--record_directory=RUN-DIR] [--source_manifest=SRC.json]
```

Every operation -- addition or deletion -- is described the same way: a changeset
file, `{"operations": [{"type": "add_reaction" | "delete_reaction", ...}]}`. MVP
scope holds the array to exactly one operation; a changeset of any other length is
refused rather than silently truncated. `delete_reaction`'s operation is `{"type":
"delete_reaction", "reaction_id": "ID"}`, nothing more -- there is no stoichiometry
or bounds to specify for a removal, and it refuses an identifier absent from the
baseline. `check` and `export` take no separate operation argument: the
changeset's own `type` field selects which invariant the candidate is checked
against, so there is nothing for a caller to keep in agreement with the file
by hand.

`resolve` returns every plausible match with the reason it matched and never picks a
winner; choosing between candidates is the caller's judgment. `check` runs the
candidate against the request and reports elemental/charge balance and FBA
feasibility under the model's own bounds and objective; every payload from `check`
carries `"scope": "structural"`. `export` re-checks the candidate, additionally
compares MEMOTE consistency results (stoichiometric consistency, mass/charge
balance, blocked reactions, dead-end and orphan metabolites) between the baseline
and the staged deliverable, and its payload carries `"scope": "export"` instead --
so a caller reading either payload can tell which layers actually ran without
consulting this README. `export` stages the deliverable, reads it back and checks
it again, then publishes without overwriting anything at the destination.
`export` requires the `memote` optional dependency group (`uv sync --extra
memote`) and typically takes one to several minutes on a genome-scale model,
dominated by flux variability analysis for blocked reactions. `--source_manifest`
asserts which model the caller expected; the result's `baseline_verified_against`
names the guarantee actually obtained.

Pass the `candidate_sha256` reported by `add_reaction` or `delete_reaction` to
`export` when the commands are separate. This pins the candidate across that gap;
export still verifies it again immediately before publication.

`record_directory` is optional and preserves stdout/API behavior. `check` records
`semantic_diff.json` and `local_checks.json`; `export` records the two MEMOTE
snapshots and `validation_summary.json`, publishing `result.xml` only after all
validation passes. Each package artifact is written independently with staged,
no-clobber semantics. Hermes owns request, trajectory, summary, and run-status files;
an absent `run_status.json` means the session record is incomplete.

A deletion that structurally passes `check` can still be refused by `export`: on
this project's frozen model, deleting `ACKr` leaves the model solvable with an
unchanged growth rate (`check` passes), but MEMOTE's before/after comparison finds
that `PTAr` -- previously `ACKr`'s only other consumer of `actp_c` -- becomes
newly blocked. `export` refuses to publish that candidate. A change can be
locally well-formed and still degrade the network in a way only a whole-model
check catches. The reverse case is documented too:
[`examples/delete-reaction/`](examples/delete-reaction/) removes
`ALAt2pp_copy2` -- a documented orphan duplicate of the gene-associated
`ALAt2pp_copy1` -- and passes both `check` and `export` cleanly, with an
honestly-scoped account of what the evidence for that case does and does not
establish.

The CLI is a thin wrapper: it calls `build_candidate` and `publish_deliverable` from
the package, so a Python caller gets the same guarantees. See
[`skills/gem-maintenance/references/tool-interface.md`](skills/gem-maintenance/references/tool-interface.md)
for the full contract.

Errors carry a category so a caller can tell them apart without parsing prose:

| Category | Meaning | Recovery |
| --- | --- | --- |
| `insufficient_information` | A name is ambiguous or a field is absent | Ask a specific question |
| `request_violation` | The request conflicts with the model or a rule | The request is wrong; more facts will not help |
| `validation_failed` | A candidate did not pass, or could not be fully checked | Regenerate from the untouched baseline; if the checks could not run, report what was undecidable |
| `model_integrity` | A baseline digest mismatch, or a write would clobber it | Stop; the inputs are not what they claim |
| `dependency_missing` | `export` requires MEMOTE, which is not installed | Install `uv sync --extra memote`; the request and candidate may be fine |

See [`examples/add-reaction/`](examples/add-reaction/) for the changeset and its
evidence, [`examples/scenarios/`](examples/scenarios/) for requests the workflow is
expected to refuse or correct, [`examples/records/`](examples/records/) for the CLI
output each one produces, and [`docs/case-selection.md`](docs/case-selection.md) for
why this case was chosen.

Regenerate the citation list after editing `docs/references.json`:

```bash
uv run python scripts/render_citations.py render
```

Tests and linting:

```bash
uv run pytest -q
uvx ruff check .
```

## Layout

| Path | Contents |
| --- | --- |
| `src/hermes_gem_maintenance/` | Package API and CLI |
| `skills/gem-maintenance/` | Hermes skill driving the CLI from a request |
| `scripts/` | Walkthrough and citation renderer |
| `examples/add-reaction/` | Frozen model, changeset, evidence |
| `examples/delete-reaction/` | A genuine deletion case: `ALAt2pp_copy2`, passing end to end |
| `examples/scenarios/` | Requests the workflow should refuse or correct |
| `examples/records/` | Recorded CLI output for the example and scenarios |
| `docs/` | Case selection, reference data, citation style |
| `instructions/` | Code standards for this repository |
| `tests/` | Behaviour checks |
| `runs/` | Per-run outputs, not tracked |

## Acknowledgements

This work builds on publicly available models, data and software:

- The `iEC1372_W3110` reconstruction (Monk *et al.*, 2016), distributed by BiGG
  Models (King *et al.*, 2016) under [its licence](http://bigg.ucsd.edu/license).
- The synthetic non-oxidative glycolysis work that motivates the example reaction
  (Bogorad *et al.*, 2013).
- COBRApy (Ebrahim *et al.*, 2013) for model handling.
- The Bioinformatics citation style from the Zotero style repository, by Julian
  Onions with contributions from Sebastian Karcher, used under CC BY-SA 3.0.

Full citations: [CITATIONS.md](CITATIONS.md).

> This is a Hermes experiment project. Commits created by the agent carry a
> `Co-authored-by: Hermes Agent <noreply@nousresearch.com>` trailer alongside the
> configured human author.
