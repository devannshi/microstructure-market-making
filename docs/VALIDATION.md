# Validation record

## Numerical and causal checks

The test suite contains hand-verifiable cases for:

- signed cash updates and exact bid/ask round trips;
- maker fees, rebates, terminal taker fees, and closing spread;
- delayed activation, old-order exposure during cancellation, and same-timestamp ineligibility;
- volume-limited partial fills, queue depletion, integer quantities and participation caps;
- no unobserved cancellation credit and retention of unchanged-order queue priority;
- stale post-only rejection and inventory limits across repeated fills;
- causal prefix invariance under changes to later prices;
- scheduled session ends, quiet final gaps, and metadata-preserving CSV round trips;
- calibration on controlled counts, including zeros and explicit all-zero fallback;
- paired bootstrap cancellation of common path noise;
- complete streamed archive SHA-256 verification, timestamp regressions, and preservation of sub-tick observations while new orders stay on the configured grid.

The suite is offline. It uses small known-answer tapes rather than tests that merely reproduce the implementation's calculations.

## Additional validation

An additional validation pass reran all 56 tests available at that time and recomputed saved synthetic results. The accounting identity

```text
net cash = spread capture + inventory revaluation − all fees − closing spread
```

reconciled across every core and stress row within 7.4e-12 tick–lots in that reviewed run. Recomputed paired means exactly matched the saved comparison table, and every path respected the 10-lot synthetic cap.

Two findings were addressed: the neutral CSV round trip now preserves price/quantity units and the scheduled horizon, and the synthetic imbalance feature is explicitly documented as lagged/nonpredictive rather than advertised as a valid predictive signal. The generator was not redesigned after seeing its unfavorable feature result.

The final experiment manifests are regenerated against frozen source. A run refuses to issue a fresh manifest if package source changes while it is executing. Final runtime and source hashes are in `results/manifest.json` and `results/historical_manifest.json`.

## Reconcile the committed outputs

After reproducing both studies, run `python scripts/verify_results.py`. It checks that both manifests match current package source, reconciles every cash row, verifies path counts and risk caps, independently recomputes paired means, and confirms exact fee sensitivity and break-even identities. It writes `results/verification.json`.

## Scope of the evidence

Accounting correctness and causal event ordering are necessary, but do not prove the queue model matches an exchange. Statistical intervals quantify the chosen synthetic experiment, not unmeasured real-world model error. Actual market records establish data provenance and observed conditions; they do not identify the counterfactual queue of a hypothetical order. These boundaries are part of the research result.

## Final artifact reconciliation

The frozen-source release contains 1,024 core rows, 4,608 stress rows, and 1,728 historical scenario rows. `scripts/verify_results.py` verified the common source digest, risk caps, paired means, conditional fee identities, and provider metadata. The maximum cash-accounting residual over all 7,360 rows is 1.85e-09 tick–lots. The seven-code-cell notebook executed without errors. Eight figure families are provided in both PNG and SVG and were visually inspected.

Terminal inventory exceeds recorded top-level depth in 44 of 1024 synthetic core outcomes and 4 of 144 historical reference block-policy outcomes. Those P&Ls still use the final-BBO closing proxy; deeper liquidation cost is not identified by this input. The flags are retained in the tables, so these cases can be excluded or assigned an additional closing-cost stress in a follow-up protocol.
