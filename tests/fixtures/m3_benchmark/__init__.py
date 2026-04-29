"""M3 benchmark corpus for the cosmetic-vs-semantic drift classifier.

The plan called for 30 paired files on disk (one per fixture). After
implementation we collapsed the corpus to a single ``cases.py``
module: 30 cases as a list of dicts with ``language``, ``name``,
``old``, ``new``, ``expected``. Same fixture coverage, one file
instead of 60, easier to maintain, identical contract for
``test_m3_benchmark.py``.
"""
