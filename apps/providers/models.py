"""Provider registry entities: DataProvider and ProviderEntityMap.

Columns and constraint/index names match CRICKET_DATABASE_SCHEMA §3.8 exactly.
``ProviderEntityMap.internal_id`` is a plain UUID (polymorphic reference) —
never an FK — so the map can point at any entity type without schema coupling.
"""

from __future__ import annotations

from django.db import models

from apps.core.models import UUIDPrimaryKeyModel

#: The entity types the identity map may reference (DDL check constraint).
ENTITY_TYPES = ("player", "team", "venue", "series", "match", "competition", "season")


class DataProvider(UUIDPrimaryKeyModel):
    name = models.TextField()
    code = models.TextField()
    base_url = models.TextField(null=True, blank=True)
    auth_kind = models.TextField(null=True, blank=True)
    # Reference to a stored credential only — never the secret itself.
    credential_ref = models.TextField(null=True, blank=True)
    supports_push = models.BooleanField(default=False)
    supports_ball_by_ball = models.BooleanField(default=False)
    supports_commentary = models.BooleanField(default=False)
    licensing_notes = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "data_provider"
        constraints = [
            models.UniqueConstraint(fields=["code"], name="ux_data_provider_code"),
        ]

    def __str__(self) -> str:
        return self.code


class ProviderEntityMap(UUIDPrimaryKeyModel):
    """Maps (provider, entity_type, provider_entity_id) -> internal UUID.

    ``internal_id`` is the platform's UUID for the entity (polymorphic, no FK).
    ``ux_pem`` makes identity resolution concurrency-safe: simultaneous
    resolves of the same provider entity collapse onto one row.
    """

    # db_index=False: the DDL declares no index on provider_id (ux_pem and
    # ix_pem_internal cover the lookup paths).
    provider = models.ForeignKey(
        "providers.DataProvider",
        on_delete=models.PROTECT,
        related_name="entity_maps",
        db_index=False,
    )
    # Enum-like column; the DDL type is `text`.
    entity_type = models.TextField()
    internal_id = models.UUIDField()
    provider_entity_id = models.TextField()
    provider_unique_name = models.TextField(null=True, blank=True)
    confidence = models.DecimalField(max_digits=4, decimal_places=3, default=1.0)
    is_primary = models.BooleanField(default=False)
    verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "provider_entity_map"
        constraints = [
            models.UniqueConstraint(
                fields=["provider_id", "entity_type", "provider_entity_id"],
                name="ux_pem",
            ),
            models.CheckConstraint(
                condition=models.Q(entity_type__in=ENTITY_TYPES),
                name="ck_provider_entity_map_entity_type_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["entity_type", "internal_id"], name="ix_pem_internal"),
        ]

    def __str__(self) -> str:
        return f"{self.provider}/{self.entity_type}:{self.provider_entity_id}"


__all__ = ["ENTITY_TYPES", "DataProvider", "ProviderEntityMap"]
