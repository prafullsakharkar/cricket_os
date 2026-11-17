"""Provider adapters that plug into the ingestion pipeline.

Each adapter registers a pipeline factory via
:func:`ingestion.services.pipeline.register_pipeline`; the management commands
and Celery tasks dispatch through the registry by name.
"""
