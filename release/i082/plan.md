# i082 release scope and existing plan

Source snapshot: e1b10f6f10f1c1d27f5f176f8b4e73ce8aa07fc2a9ab94b9eaf2d9cd10bd7c74. Prepared 2026-10-04T22:41:08+08:00.

This source release uses the frozen i082 implementation and its recorded test evidence. The planning entry defaults to feature-profile i082; final_defaults.py defines the previous nine final-entry mechanisms, four i081 mechanisms and the i082 planner settings. Explicit user options retain precedence.

The i082 additions are equally spaced paired-card sampling at entries 1,32,64,96,..., duplicate table suppression, equal-weight mean merging of complete tables, and paired added-card rows for the joint model. Duplicate suppression skips synthetic sampling work; it does not establish state equivalence or prune real routes. Prioritizing current elites was considered and not implemented.

Joint rows use each added-card arm's own five F2 samples and borrow the base entry's real F1 result. This assumes zero effect of the extra card on F1. Rows from the same table are correlated and the same data also contributes to paired card tiers; the joint row count includes synthetic rows.

Existing acceptance is the 845/845 regression report, preserved as [tests.json](tests.json). Frozen identity and selected files are recorded in [freeze.json](freeze.json); exact profile values are in [defaults.json](defaults.json). The exporter has not started a new build, probe, campaign or batch.

The public command documentation recommends one seed at a 30-minute cap. The existing user diagnostic i082-60-524130501 was explicitly launched with a 60-minute cap; its budget is retained unchanged in [existing-run-progress.json](existing-run-progress.json). The separate release20 study is conditional on a verified source-run victory. This export does not trigger it.

Original plan reference: P5plus-f1-card-switches/experiments/iteration-082/plan.md.
Original plan SHA-256: 6c4437a45633ccacb08a1af0b1a8607b2e11a12ce156b9ec5eecfdb6ff2f3076.
This Markdown is a sanitized summary, not the original byte stream.
