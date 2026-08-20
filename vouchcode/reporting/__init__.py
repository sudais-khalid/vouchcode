"""Layer 5, Reporting. Phase 5.

Compiles the ledger, or a specified commit range, into two artifacts: a machine-readable
signed JSON document and a human-readable PDF summarizing aggregate authorship
percentages and comprehension scores. Both embed the public key needed to verify the
Ed25519 signatures, so a recipient can check the report offline without installing
Vouchcode and without holding an account, per Section 4.5.

Portability of proof is the point. A report that requires the verifier to trust the
sender, or to log in somewhere, does not solve the problem stated in Section 1.2.
"""
