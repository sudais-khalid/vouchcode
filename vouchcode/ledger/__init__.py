"""Layer 4, Ledger.

Records the outcome of each commit cycle, comprising attribution results and, from
Phase 3 onward, comprehension scores. Phase 1 establishes the append-only JSON store and
the entry schema. Phase 4 adds the hash chain and Ed25519 signatures that make the
history tamper-evident, per Section 4.4 of the research documentation.
"""
