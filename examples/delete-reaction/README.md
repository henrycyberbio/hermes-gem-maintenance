# Delete-reaction case: `ALAt2pp_copy2`

## The reaction

`ALAt2pp_copy2` — L-alanine transport in via proton symport (periplasm),
`ala__L_p + h_p <=> ala__L_c + h_c`, reversible, bounds `(-1000.0, 1000.0)`, no
gene association (`gene_reaction_rule: ""`).

The same model carries `ALAt2pp_copy1`, the same reaction irreversible
(`ala__L_p + h_p --> ala__L_c + h_c`, bounds `(0.0, 1000.0)`) and associated with
gene `Y7U_RS21895` (the *E. coli* alanine/glycine symporter `cycA`).

## Evidence and its honest limit

This is a documented pattern in BiGG-derived *E. coli* reconstructions, not a
one-off observation of this model alone:

- The BiGG Models database itself flags `ALAt2pp` as "appears 2 times" in every
  *E. coli* K-12-derived model that carries it (`iS_1188`, `iZ_1308`, `iSF_1195`,
  `iAF1260b`, and others), always as the same `_copy1` (gene-associated,
  irreversible) / `_copy2` (no gene, reversible) split — this is a stable feature
  of this reconstruction lineage, not a one-off artifact of this particular model.
- Orth *et al.* 2011 ("A comprehensive genome-scale reconstruction of
  *Escherichia coli* metabolism—2011", *Mol Syst Biol* 7:535 — the iJO1366
  reconstruction that this model's lineage descends from) documents "orphan
  reactions" (reactions with no associated gene) as a formal, quantified category
  of this reconstruction: Table 1 of that paper reports 58 of 778 transport
  reactions in iJO1366 (6%) carry no gene association. `ALAt2pp_copy2`'s empty
  `gene_reaction_rule` places it in this documented category.

**What this evidence does not establish**: the paper defines "orphan reaction"
and quantifies how many exist, but does not state a specific mechanistic reason
for *this* `_copy1`/`_copy2` duplicate-pair pattern -- an earlier line of inquiry
in this project's own research considered the hypothesis that such duplicates
were kept during automated gap-finding specifically to preserve flux-consistency,
but no primary source consulted here confirms that mechanism for this reaction
pair, and that hypothesis is not repeated here as established fact. No source
consulted says, in so many words, "delete `ALAt2pp_copy2` from
`iEC1372_W3110`." The claim actually supported is narrower: this reaction is
recognizable as an instance of a formally defined, quantified reconstruction
category (an orphan duplicate of a gene-associated reaction with identical
stoichiometry), and removing it is expected to leave the network's alanine
transport capacity unchanged because `ALAt2pp_copy1` already carries the same
stoichiometry with real gene support. That expectation is what this case
actually verifies -- not a literature-sourced curation decision to remove this
specific reaction.

## Structural check: is the redundancy real?

Both `ala__L_c` and `ala__L_p` remain connected to several other reactions after
removing `ALAt2pp_copy2` (`ALATRS`, `ALAt4pp`, `ALAabcpp`, `ALAtex`, and more) —
`ALAt2pp_copy2` is not the sole route between these compartments for this
metabolite, so its removal is not expected to strand anything on that basis
alone. The MEMOTE regression check confirms this quantitatively (below), rather
than relying on this manual connectivity read as the actual verdict.

## Result

Verified against the frozen model, `examples/add-reaction/model/iEC1372_W3110.xml`
(baseline SHA-256 `109290d2e2407a94f8088f9ef9fd40f6db6b73b1cf36574d6d987527ece8d9b7`),
2026-09-09:

- `check`: passed. Model remains solvable, objective (biomass) essentially
  unchanged: `0.9823963461343078` (baseline) → `0.9823963461343095` (candidate) —
  the ~2e-12 difference is solver floating-point noise, not a biological change.
- `export`: passed. `consistency_regression.ok: true` — no new blocked reactions,
  no new dead-end or orphan metabolites, no new mass/charge imbalance,
  stoichiometric consistency unchanged. Delivered digest:
  `14febf5f142e876bab50a879a33b1122c02c6efc5707ca05ac5d798951fa7178`.

This is the positive counterpart to the `ACKr` deletion documented in the
project's README, which `export` correctly *refuses* (deleting `ACKr` strands
`PTAr`). Together the two cases show both directions of the three-layer design:
a deletion that structurally checks out and stays clean end to end, and one that
structurally checks out but is caught by the whole-model regression layer.
