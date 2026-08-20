"""Layer 3, Comprehension. Phase 3.

For each hunk attributed to AI generation, extracts verifiable structural facts from the
hunk's control flow, derives questions from those facts, prompts the developer in the
terminal, and scores the answers by matching key structural terms against the extracted
facts.

Section 3.1 forbids any external language model in this path. Questions are derived from
the code's own structure and scoring is term matching against extracted facts, not
semantic similarity to a generated reference answer. Section 4.3 records the tradeoff
this buys and costs: determinism and reproducibility, at the price of tolerating less
linguistic flexibility in an acceptable answer.
"""
