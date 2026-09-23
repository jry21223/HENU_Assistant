# Production snapshot startup compatibility

The deployed plugin has a snapshot-v2 Storage adapter not present in this older published branch. Do not replace it with this branch's non-snapshot adapter.

The accompanying patch records the reviewed September 23 startup hotfix against the previously deployed snapshot adapter (already containing yuketang file materialization). It adds the rain-classroom record to startup reconciliation, and reconciles a pre-feature snapshot against its original key set only when the new key is absent in both snapshot and legacy storage. All other schema and drift checks remain strict.

Apply only after checking that the target contains `reconcile_legacy_snapshots` and matches the patch context. Back up code and data first. Do not apply blindly to an older non-snapshot checkout.

Verified regressions on the production overlay: new snapshots reconcile with byte-identical Storage; old snapshots missing BOTH new records start successfully and preserve the old profile; normal materialization still supplies an empty new config; credential confirmation and failed-save/no-bridge-write regressions pass. Deployment backup: `/root/LangBot/backups/yuketang-password-hint-20260923/`.
