"""Metric-driven long-running campaigns (#930).

A campaign repeats cycle Tasks toward a measured goal until the target is
confirmed, the deadline passes, the budget runs out or a person stops it.
``policy`` holds the rules, ``service`` the state machine and writes,
``scheduler`` the loop that opens cycles and ``brief`` the cycle brief.
"""
