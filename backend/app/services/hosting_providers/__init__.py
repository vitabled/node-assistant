"""Hosting-provider API adapters for the infra-billing subsystem (Wave-9 Plan C).

One module per vendor; `base.py` holds the contract every adapter implements and
`registry.py` exposes them by `kind`. Import adapters through the registry — it
tolerates a missing module, so the pack can grow one vendor at a time.
"""
