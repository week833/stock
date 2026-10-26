"""Suite-wide pytest configuration.

``_retired`` keeps, verbatim, the tests whose module or feature was intentionally removed by the
current-only / C0 redesign (see ``_retired/README.md``).  They are kept for the record and are never
collected.
"""

collect_ignore = ["_retired"]
