"""Adapters that translate an AI coding assistant's own events into Vouchcode signals.

An adapter's only job is to write the signal file format defined by
vouchcode.capture.signals. It never classifies, never scores, and never reads back into
Vouchcode's attribution logic. That separation is what keeps the signal format the
single integration point: a new assistant needs a new adapter and nothing else.

Adapters are deliberately narrow. They record which file an assistant touched and when.
They do not capture prompts, transcripts, conversation content, or anything else the
assistant knows, because none of that is needed to attribute a hunk and all of it would
be a privacy liability sitting in a repository directory.
"""
