"""Provider adapters (DATA_INGESTION §2).

``replay`` is the internal fake provider that replays recorded (Phase C)
matches through the live pipeline. Licensed-provider adapters arrive after the
Phase 0 gate (docs/PROVIDER_DECISION.md) and must implement the same
``ProviderAdapter`` protocol; the reusable contract suite in
``tests/contracts/provider_adapter_kit.py`` pins that contract so a new
adapter is verified against exactly the shape the live path consumes.
"""
