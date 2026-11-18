# Contributing

Use Python 3.10 or newer, install `.[dev]`, and run `pytest` before changing execution behavior. A change to event ordering, queue treatment, accounting, or causal calibration needs a hand-verifiable regression case. Keep synthetic tests offline. Preserve data-provider attribution and do not commit raw or normalized exchange caches.

Before comparing a new policy, state its data requirements, train/evaluation split, objective, parameters, and failure criteria in an experiment protocol. Save every tested scenario, including poor outcomes. Do not select reporting windows from test P&L. A proposed performance improvement needs paired held-out evidence; an execution-correctness change needs an accounting or ordering argument.
