"""Whole-model consistency review, compared before and after a change.

MEMOTE's own pytest suite (`memote.suite.api.test_model`) runs annotation, SBO,
growth and essentiality checks alongside consistency -- essentiality alone runs one
single-gene deletion per gene and is minutes slower on a genome-scale model, and none
of those categories are the ones a single-reaction addition can be judged against.
The plan (`local/Hermes-GEM_Plan_v0.1.md`, S10.2) names five specific categories as
the acceptance gate: stoichiometric consistency, mass balance, charge balance,
blocked reactions, and dead-end/orphan metabolites. This module calls MEMOTE's
underlying analysis functions directly for exactly those categories, producing a
structured snapshot this package can diff -- not a report meant for human reading.

A total score, or a total count of failures, is not read anywhere in this module.
Comparing totals lets an improvement in one metabolite hide a regression in another;
every comparison here is a set difference against the same category.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from hermes_gem_maintenance.errors import DependencyMissingError

if TYPE_CHECKING:
    import cobra


# ==== snapshot ====


@dataclass(frozen=True)
class ConsistencySnapshot:
    """The five hard-check categories, computed once against one model."""

    stoichiometrically_consistent: bool
    mass_unbalanced: frozenset[str]
    charge_unbalanced: frozenset[str]
    blocked_reactions: frozenset[str]
    dead_end_metabolites: frozenset[str]
    orphan_metabolites: frozenset[str]


def consistency_snapshot(model: cobra.Model) -> ConsistencySnapshot:
    """Compute the five categories against one model.

    Requires the optional `memote` dependency group (`pip install .[memote]`). A
    missing import is reported inside the package's own error taxonomy rather than
    as a bare ModuleNotFoundError, so a caller handling `GemMaintenanceError` sees a
    `dependency_missing` category instead of an exception type it never subscribed to.

    `find_blocked_reactions` runs with `processes=1, open_exchanges=True`.
    `open_exchanges=True` matches MEMOTE's own `test_blocked_reactions`, which defines
    "universally blocked" as blocked under complete medium (every boundary reaction
    opened) -- on this project's baseline model the model's own configured medium
    reports roughly three times as many blocked reactions as MEMOTE's definition, so
    using the default would silently answer a different, stricter question than the
    one MEMOTE reports and this gate claims to mirror. `processes=1`: flux
    variability analysis's default multiprocessing pool uses the spawn start method
    on Windows, which fails when this function executes from within an
    already-running interpreter (`python -c "..."`, or any embedding context) rather
    than a `__main__` script. Running single-process is slower -- tens of seconds on
    a genome-scale model -- but correct in every calling context, which matters more
    for a one-off delivery gate than the extra wall-clock time.
    """
    try:
        import memote.support.consistency as consistency
        import memote.support.consistency_helpers as con_helpers
        from cobra.flux_analysis import find_blocked_reactions
        from memote.utils import get_ids
    except ImportError as exc:
        msg = "memote is required for delivery; install with pip install .[memote]"
        raise DependencyMissingError(msg, missing="memote") from exc

    internal = con_helpers.get_internals(model)
    return ConsistencySnapshot(
        stoichiometrically_consistent=consistency.check_stoichiometric_consistency(
            model
        ),
        mass_unbalanced=frozenset(
            get_ids(consistency.find_mass_unbalanced_reactions(internal))
        ),
        charge_unbalanced=frozenset(
            get_ids(consistency.find_charge_unbalanced_reactions(internal))
        ),
        blocked_reactions=frozenset(
            find_blocked_reactions(model, processes=1, open_exchanges=True)
        ),
        dead_end_metabolites=frozenset(get_ids(consistency.find_deadends(model))),
        orphan_metabolites=frozenset(get_ids(consistency.find_orphans(model))),
    )


# ==== regression ====


@dataclass(frozen=True)
class ConsistencyRegression:
    """What got worse between two snapshots -- never what a total score did.

    Stoichiometric consistency is compared as a transition, not a fixed target: a
    model that was already inconsistent before the change (a real, pre-existing
    property of some published reconstructions) is reported as inconsistent without
    being treated as this change's fault, but a consistent model becoming
    inconsistent is a genuine regression this change introduced.
    """

    stoichiometric_consistency_lost: bool
    new_mass_unbalanced: tuple[str, ...]
    new_charge_unbalanced: tuple[str, ...]
    new_blocked_reactions: tuple[str, ...]
    new_dead_end_metabolites: tuple[str, ...]
    new_orphan_metabolites: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """True only when nothing in the tracked categories got worse."""
        return not (
            self.stoichiometric_consistency_lost
            or self.new_mass_unbalanced
            or self.new_charge_unbalanced
            or self.new_blocked_reactions
            or self.new_dead_end_metabolites
            or self.new_orphan_metabolites
        )

    def as_dict(self) -> dict[str, Any]:
        """Structured form for JSON output."""
        return {
            "stoichiometric_consistency_lost": self.stoichiometric_consistency_lost,
            "new_mass_unbalanced": list(self.new_mass_unbalanced),
            "new_charge_unbalanced": list(self.new_charge_unbalanced),
            "new_blocked_reactions": list(self.new_blocked_reactions),
            "new_dead_end_metabolites": list(self.new_dead_end_metabolites),
            "new_orphan_metabolites": list(self.new_orphan_metabolites),
            "ok": self.ok,
        }


def compare_consistency(
    before: ConsistencySnapshot, after: ConsistencySnapshot
) -> ConsistencyRegression:
    """Diff two snapshots, reporting only what newly appeared or was lost."""
    return ConsistencyRegression(
        stoichiometric_consistency_lost=(
            before.stoichiometrically_consistent
            and not after.stoichiometrically_consistent
        ),
        new_mass_unbalanced=tuple(
            sorted(after.mass_unbalanced - before.mass_unbalanced)
        ),
        new_charge_unbalanced=tuple(
            sorted(after.charge_unbalanced - before.charge_unbalanced)
        ),
        new_blocked_reactions=tuple(
            sorted(after.blocked_reactions - before.blocked_reactions)
        ),
        new_dead_end_metabolites=tuple(
            sorted(after.dead_end_metabolites - before.dead_end_metabolites)
        ),
        new_orphan_metabolites=tuple(
            sorted(after.orphan_metabolites - before.orphan_metabolites)
        ),
    )
