# Recorded responses

Every file in `cassettes/` is the body of one HTTP response from the ECB Data Portal,
**stored exactly as it arrived**. Nothing is parsed, reshaped, filtered or tidied. `index.json`
records, for each one, the URL that produced it, the status, the content type, the byte count,
the SHA-256 of the body, every response header, and when it was captured.

These are **test fixtures for replaying a handful of exchanges**, not a dataset and not a
redistribution service. Anyone who wants the data should get it from the ECB, free, at
<https://data.ecb.europa.eu>.

**Source: ECB statistics.**

Reused under the [ESCB policy on the reuse of statistics][policy], which permits free reuse on
three conditions, each of which is a test in this repository rather than a promise here:

1. **the source is quoted**, which it is on this page, in the README and in `index.json`;
2. **the statistics and metadata are not modified**, which storing the raw bytes guarantees;
3. **no third-party data**, so this stays on `EXR`, the ECB's own compilation.

[policy]: https://www.ecb.europa.eu/stats/ecb_statistics/governance_and_quality_framework/html/usage_policy.en.html
