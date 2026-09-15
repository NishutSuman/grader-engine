"""Generic, config-driven assignment grading agent.

Each grading job lives in its own ``runs/<slug>/`` directory containing a
``config.yaml`` that defines the rubric, branding, and how to read the
submission sheet. Every module in this package is domain-agnostic: it reads all
assignment-specific knowledge from that config.
"""
