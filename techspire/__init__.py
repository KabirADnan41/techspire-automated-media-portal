"""Techspire Official - automated technology-news curation.

Pipeline: RSS -> normalize -> SQLite dedupe -> AI curator -> 1200x630 card
-> local review (READY_FOR_REVIEW). Facebook publishing exists but is locked
behind three independent gates (see fb_publisher.py).
"""

__version__ = "1.0.0"
