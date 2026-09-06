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
skill in `skills/gem-maintenance/` drives them from a natural-language request. It has
been exercised by agents given only the request and the skill — no access to the
source, tests, or worked answer — on the successful case, on two requests it should
refuse, and on one it should correct before completing; see
[`examples/scenarios/`](examples/scenarios/).

Those exercises cover the paths an agent takes with a well-formed request. Two rounds
of adversarial review of the package itself found defects they could not reach:
delivery of a candidate whose balance check never ran, unchecked reaction metadata,
exact identifiers made unresolvable by substring noise, malformed JSON escaping the
error taxonomy, a "check" that accepted a candidate which had added nothing, a
deliverable left on disk after a failed integrity check, and case-folding that
conflated `CO` with `Co`. All are fixed and pinned by regression tests. Treat
behavioural exercises and adversarial review as answering different questions.

## Use

```bash
uv sync
```

### The worked example

```bash
uv run python scripts/add_reaction_walkthrough.py run
```

Adds reaction `PKETF` to the frozen `iEC1372_W3110` model and writes `candidate.xml`
plus `checks.json` to `runs/PKETF/`. Eight checks run: reaction present,
stoichiometry, bounds, gene rule, elemental and charge conservation, no unrelated
semantic changes, SBML roundtrip, input unchanged.

### The commands

Each subcommand prints JSON and exits non-zero on a deliberate error.

```bash
uv run hermes-gem-maintenance inspect  --model=MODEL [--reaction=ID | --metabolite=ID]
uv run hermes-gem-maintenance resolve  --model=MODEL --query=NAME [--compartment=C]
uv run hermes-gem-maintenance add_reaction --model=MODEL --reaction=SPEC.json --output=CAND.xml [--source_manifest=SRC.json]
uv run hermes-gem-maintenance check    --model=MODEL --candidate=CAND.xml --reaction=SPEC.json
uv run hermes-gem-maintenance export   --model=MODEL --candidate=CAND.xml --reaction=SPEC.json --output=OUT.xml [--source_manifest=SRC.json]
```

`resolve` returns every plausible match with the reason it matched and never picks a
winner; choosing between candidates is the caller's judgment. `export` re-checks the
candidate as loaded from disk and refuses to write a deliverable unless every check
ran and passed. A check that could not be decided blocks delivery too. Deliverables
are published by atomic rename after the final baseline check, so a failed run leaves
nothing at the output path.

`--source_manifest` is how a caller asserts *which* model it expected. Without it the
tool can only confirm the file did not change while the command ran; the result's
`baseline_verified_against` field names the guarantee actually obtained.

Errors carry a category so a caller can tell them apart without parsing prose:

| Category | Meaning | Recovery |
| --- | --- | --- |
| `insufficient_information` | A name is ambiguous or a field is absent | Ask a specific question |
| `request_violation` | The request conflicts with the model or a rule | The request is wrong; more facts will not help |
| `validation_failed` | A candidate did not pass, or could not be fully checked | Regenerate from the untouched baseline; if the checks could not run, report what was undecidable |
| `model_integrity` | A baseline digest mismatch, or a write would clobber it | Stop; the inputs are not what they claim |

See [`examples/add-reaction/`](examples/add-reaction/) for the request and its
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
| `examples/add-reaction/` | Frozen model, reaction definition, evidence |
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
