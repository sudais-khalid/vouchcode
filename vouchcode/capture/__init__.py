"""Layer 1, Capture.

Installs and owns the git hooks that intercept commit events, and reads the change set
associated with each commit. Where a supported AI coding assistant exposes session or
acceptance metadata, that signal is the primary attribution source; absent one, the
stylometric fallback in vouchcode.capture.stylometry produces a confidence-scored
best-effort classification. Both attribution paths are Phase 2 work.
"""
