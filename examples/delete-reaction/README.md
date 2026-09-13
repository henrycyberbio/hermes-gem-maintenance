# Delete-reaction cases

Three deletions verified against the frozen model,
`examples/add-reaction/model/iEC1372_W3110.xml` (baseline SHA-256
`109290d2e2407a94f8088f9ef9fd40f6db6b73b1cf36574d6d987527ece8d9b7`). Together they
show all three ways `delete_reaction` is expected to behave: a clean pass, a
structural pass caught by the whole-model MEMOTE regression, and a structural
failure caught before MEMOTE ever runs.

## Positive case: `ALAt2pp_copy2` — passes `check` and `export`

`ALAt2pp_copy2` — L-alanine transport in via proton symport (periplasm),
`ala__L_p + h_p <=> ala__L_c + h_c`, reversible, bounds `(-1000.0, 1000.0)`, no
gene association (`gene_reaction_rule: ""`).

The same model carries `ALAt2pp_copy1`, the same reaction irreversible
(`ala__L_p + h_p --> ala__L_c + h_c`, bounds `(0.0, 1000.0)`) and associated with
gene `Y7U_RS21895` (the *E. coli* alanine/glycine symporter `cycA`).

### Evidence and its honest limit

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

### Structural check: is the redundancy real?

Both `ala__L_c` and `ala__L_p` remain connected to several other reactions after
removing `ALAt2pp_copy2` (`ALATRS`, `ALAt4pp`, `ALAabcpp`, `ALAtex`, and more) —
`ALAt2pp_copy2` is not the sole route between these compartments for this
metabolite, so its removal is not expected to strand anything on that basis
alone. The MEMOTE regression check confirms this quantitatively (below), rather
than relying on this manual connectivity read as the actual verdict.

### Result (verified 2026-09-09)

- `check`: passed. Model remains solvable, objective (biomass) essentially
  unchanged: `0.9823963461343078` (baseline) → `0.9823963461343095` (candidate) —
  the ~2e-12 difference is solver floating-point noise, not a biological change.
- `export`: passed. `consistency_regression.ok: true` — no new blocked reactions,
  no new dead-end or orphan metabolites, no new mass/charge imbalance,
  stoichiometric consistency unchanged. Delivered digest:
  `14febf5f142e876bab50a879a33b1122c02c6efc5707ca05ac5d798951fa7178`.

## MEMOTE-regression negative case: `ACKr` — passes `check`, refused by `export`

`ACKr` (acetate kinase) is present in the frozen model with `actp_c` shared
between two consumers: `ACKr` itself and `PTAr` (phosphotransacetylase).

### Result

- `check`: passed. Deleting `ACKr` leaves the model solvable with an unchanged
  growth rate — the structural layer alone cannot see anything wrong.
- `export`: refused. MEMOTE's before/after comparison finds that `PTAr` —
  previously `ACKr`'s only other consumer of `actp_c` — becomes newly blocked
  once `ACKr` is gone. `export` does not publish that candidate.

A change can be locally well-formed and still degrade the network in a way only
a whole-model check catches. This is the negative counterpart to
`ALAt2pp_copy2`: same structural layer, opposite whole-model verdict.

## Infeasibility negative case: `EX_glc__D_e` — refused by `check` itself

`EX_glc__D_e` (D-glucose exchange, bounds `(-10.0, 1000.0)`) is the sole carbon
source available to the model's default objective
(`BIOMASS_Ec_iJO1366_core_53p95M`). An exhaustive single-reaction-deletion scan of
all 2758 reactions in the frozen model (`cobra.flux_analysis.single_reaction_deletion`)
found exactly one reaction whose removal makes the model infeasible under its own
bounds and objective: this one.

### Result

- `check`: refused. `candidate is solvable under its own bounds and objective:
  infeasible, objective=0.0` is reported as a failed check, alongside the two
  structural checks that still pass (the reaction is absent from the candidate
  and present in the baseline, and the diff contains no unrelated changes).
  `export` was not run — `check` already reports the request cannot be granted,
  and there is nothing for MEMOTE to compare that would change that verdict.

This is the case the MVP's feasibility layer (`feasibility.py`) exists for: an
SBML that parses cleanly and a diff with nothing unrelated in it can still
describe a network that cannot reach any feasible flux distribution, and that is
a distinct failure mode from every check in `checks.py`.

## What the three cases together show

- `ALAt2pp_copy2`: structurally clean, and clean at the whole-model layer too —
  the case that should pass end to end.
- `ACKr`: structurally clean, but the whole-model layer catches a real
  regression the structural layer cannot see.
- `EX_glc__D_e`: caught before the whole-model layer ever runs, because the
  structural layer's own feasibility check already reports the request cannot be
  granted.

None of the three establishes a general claim about which classes of reactions
are safe to delete; each is a single verified instance of one of the workflow's
three possible outcomes for a deletion.
