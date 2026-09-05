# Hermes-GEM Maintenance

A genome-scale metabolic model (GEM) represents an organism's metabolism as a network
of genes, reactions and metabolites that can be simulated computationally. Keeping
such a model current is ongoing manual work: published findings have to be turned
into concrete reactions, mapped onto the identifiers the model already uses, and
checked for consistency before the change is accepted.

This project gives Hermes the ability to carry out one such maintenance task
end-to-end. Given a public model, a request in plain language, and the supporting
evidence, Hermes reads the model, resolves the metabolites involved, constructs the
reaction, applies it to a working copy, checks the result, and delivers the modified
model with an account of what changed.

The division of labour is deliberate. A Python package performs everything that can
be decided deterministically — reading models, listing candidates, applying a given
reaction, checking the outcome. A Hermes skill covers what cannot: interpreting the
request, judging whether a metabolite mapping is unambiguous, deciding when to ask
rather than assume, and explaining a failure. Checks live in one place, in the
package, and the skill refers to them.

Source models are treated as immutable evidence. Every operation reads the original
or writes an independent copy; the package refuses to overwrite an input.

> **Hermes experiment project.** Commits created by the agent carry a
> `Co-authored-by: Hermes Agent <noreply@nousresearch.com>` trailer alongside the
> configured human author.

## Status

Early implementation. The first case is selected and verified against the frozen
model; the package, CLI and skill are being built around it.

The proof-of-concept task introduces a heterologous phosphoketolase reaction
(`PKETF`) into the *Escherichia coli* W3110 model `iEC1372_W3110`. E. coli has no
native phosphoketolase; the enzyme is the core of the synthetic non-oxidative
glycolysis route reported by (Bogorad *et al.*, 2013), which converts hexose to
acetyl-CoA without the carbon loss of pyruvate decarboxylation. Reaction identifier
and stoichiometry follow the BiGG universal record (King *et al.*, 2016).

## Citations

Sources are listed in [CITATIONS.md](CITATIONS.md), generated from
`docs/references.json` by the vendored citation style. To re-render after editing the
reference data:

```bash
uv run python scripts/render_citations.py render
uv run python scripts/render_citations.py render --check
```

## Development

Code follows [instructions/code_requirements.instructions.md](instructions/code_requirements.instructions.md).

```bash
uv run pytest -q
```
