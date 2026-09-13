# Hermes-GEM Maintenance

Genome-scale metabolic model (GEM) maintenance turns biological evidence into a
specific, reviewable model change. A published finding may imply a new reaction,
a correction, or a deletion, but the model has its own identifiers, compartments,
and constraints. This project helps Hermes carry that work from a request and its
evidence to a separately written, validated SBML result.

The source model is never modified. Hermes interprets the request and evidence; the
package applies the requested change to a copy and checks the result against the
model, the requested semantics, and whole-model consistency criteria.

## Use

Install the project environment:

```bash
uv sync
```

Run the first proof of concept:

```bash
uv run python scripts/add_reaction_walkthrough.py run
```

The walkthrough writes a candidate model and local validation artifacts under
`runs/PKETF/`. It refuses to replace an existing run directory or the frozen source
model.

For Hermes-driven maintenance work, use the project skill in
[`skills/gem-maintenance/`](skills/gem-maintenance/). The command interface and
machine-readable result shapes are documented alongside that skill.

## First proof of concept: PKETF

The initial case adds `PKETF`, a heterologous phosphoketolase reaction, to the frozen
*iEC1372_W3110* *Escherichia coli* model. The request uses metabolites already present
in the model:

```text
f6p_c + pi_c -> actp_c + e4p_c + h2o_c
```

The case was chosen because it makes the essential workflow visible: derive a concrete
reaction from public evidence, map it onto the model, produce a new candidate, and
validate the result without changing the source artifact. The worked request,
changeset, evidence, and model provenance are in
[`examples/add-reaction/`](examples/add-reaction/).

The project also includes a positive deletion case and cases that the workflow should
refuse. They demonstrate that a change can be locally well-formed yet still fail
whole-model validation.

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
