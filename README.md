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

**Status:** early implementation. The first case is verified end-to-end; the package,
CLI and skill are being built around it.

## Use

```bash
uv sync
uv run python scripts/add_reaction_walkthrough.py run
```

Adds reaction `PKETF` to the frozen `iEC1372_W3110` model and writes `candidate.xml`
plus `checks.json` to `runs/PKETF/`. Eight checks run: reaction present,
stoichiometry, bounds, gene rule, elemental and charge conservation, no unrelated
semantic changes, SBML roundtrip, input unchanged. The command refuses a non-empty
output directory and never writes to the input.

See [`examples/add-reaction/`](examples/add-reaction/) for the request and its
evidence, and [`docs/case-selection.md`](docs/case-selection.md) for why this case
was chosen.

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
| `src/hermes_gem_maintenance/` | Package API (in progress) |
| `scripts/` | Walkthrough and citation renderer |
| `examples/add-reaction/` | Frozen model, reaction definition, evidence |
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
