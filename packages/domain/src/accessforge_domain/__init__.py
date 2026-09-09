"""Pure deterministic domain logic for AccessForge.

Everything in this package is a total function of its inputs: no database, no clock, no network,
no model. That is what makes the outcome rules auditable — a verdict can be recomputed from the
recorded evidence alone, by anyone, without access to the system that produced it.
"""
