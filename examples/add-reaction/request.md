# Request

Please add the phosphoketolase reaction to our *E. coli* W3110 model.

We are introducing the *Bifidobacterium adolescentis* `xfp` gene to give the strain a
phosphoketolase activity it does not have natively. The enzyme cleaves
fructose-6-phosphate into acetyl-phosphate and erythrose-4-phosphate, consuming
inorganic phosphate and releasing water. This is the reaction from the synthetic
non-oxidative glycolysis work (Bogorad et al., Nature 2013) — the point is to reach
acetyl-CoA without losing a carbon to pyruvate decarboxylation.

Use `PKETF` as the identifier, which is what BiGG calls it. Everything happens in the
cytosol. Treat it as irreversible in the forward direction: in the engineered strain
we only care about flux toward acetyl-phosphate, even though BiGG lists the universal
reaction as reversible. The gene rule is just `xfp`.

The model is in `examples/add-reaction/model/`. Please don't modify it — write the
result somewhere separate and tell me what changed.
