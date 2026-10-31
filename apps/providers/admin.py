"""Admin for the provider registry + the shared read-only map inline."""

from __future__ import annotations

from functools import partial
from typing import Any, cast

from django.contrib import admin
from django.contrib.admin.checks import InlineModelAdminChecks
from django.db.models import QuerySet
from django.forms.models import modelformset_factory

from apps.providers.models import DataProvider, ProviderEntityMap


@admin.register(DataProvider)
class DataProviderAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("name", "code", "is_active", "supports_push", "supports_ball_by_ball")
    search_fields = ("name", "code")
    list_filter = ("is_active", "supports_push", "supports_ball_by_ball")
    # credential_ref points at a stored credential — never the secret itself;
    # keep it visible but read-only so it cannot be edited into a leak.
    readonly_fields = ("credential_ref",)
    fieldsets = (
        (None, {"fields": ("name", "code", "base_url")}),
        (
            "Capabilities",
            {
                "fields": (
                    "supports_push",
                    "supports_ball_by_ball",
                    "supports_commentary",
                )
            },
        ),
        (
            "Credential reference (never the secret)",
            {"fields": ("credential_ref", "auth_kind", "licensing_notes")},
        ),
    )


@admin.register(ProviderEntityMap)
class ProviderEntityMapAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "provider",
        "entity_type",
        "provider_entity_id",
        "internal_id",
        "confidence",
        "is_primary",
        "verified",
    )
    search_fields = ("provider_entity_id", "provider_unique_name")
    list_filter = ("entity_type", "verified", "is_primary", "provider")
    readonly_fields = ("created_at",)


class _ProviderEntityMapInlineChecks(InlineModelAdminChecks):
    """Skip the FK check: ``internal_id`` is a plain UUID column, not an FK."""

    def _check_relation(self, obj: Any, parent_model: Any) -> list[Any]:
        return []


class ProviderEntityMapInline(admin.TabularInline):  # type: ignore[type-arg]
    """Read-only inline of a parent entity's provider identity maps.

    ``ProviderEntityMap.internal_id`` is a plain UUID column (decision D2), not
    a ForeignKey, so Django's ``inlineformset_factory`` cannot attach the rows.
    This inline is display-only: the formset is built with
    ``modelformset_factory`` (no parent FK required), accepts the admin's
    ``instance`` kwarg and ignores it, and rows are filtered by
    ``internal_id == parent pk`` (from the request URL). Add/delete are
    disabled, so it can never write.
    """

    model = ProviderEntityMap
    extra = 0
    can_delete = False
    show_change_link = True
    verbose_name_plural = "Provider identity maps"
    checks_class = _ProviderEntityMapInlineChecks

    INLINE_FIELDS = (
        "provider",
        "entity_type",
        "provider_entity_id",
        "internal_id",
        "confidence",
        "is_primary",
        "verified",
        "created_at",
    )

    def get_fields(self, request: Any, obj: Any = None) -> tuple[str, ...]:
        return self.INLINE_FIELDS

    def get_readonly_fields(self, request: Any, obj: Any = None) -> tuple[str, ...]:
        return self.INLINE_FIELDS

    def has_add_permission(self, request: Any, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        return False

    def get_queryset(self, request: Any) -> QuerySet[ProviderEntityMap]:
        qs = super().get_queryset(request)
        object_id = self._parent_object_id(request)
        if object_id is None:
            return qs.none()  # the add view has no parent yet
        return qs.filter(internal_id=object_id).order_by("-created_at")

    def get_formset(self, request: Any, obj: Any = None, **kwargs: Any) -> Any:
        # Every field is excluded from the editable form: the rows render as
        # read-only display via ``get_readonly_fields``. The formset is built
        # with ``modelformset_factory`` (BaseModelFormSet semantics — no parent
        # FK required) and then made inert: ``instance``/``save_as_new`` from
        # the admin are accepted and ignored, and every write path is a no-op.
        base = cast(
            Any,
            modelformset_factory(
                self.model,
                form=self.form,
                exclude=list(self.INLINE_FIELDS),
                extra=0,
                can_delete=False,
                formfield_callback=partial(self.formfield_for_dbfield, request=request),
            ),
        )

        class ReadOnlyFormSet(base):  # type: ignore[valid-type, misc]
            def __init__(
                self,
                *args: Any,
                instance: Any = None,
                save_as_new: bool = False,
                **kwargs: Any,
            ):
                self.instance = instance
                self.save_as_new = save_as_new
                super().__init__(*args, **kwargs)

            def save(self, commit: bool = True) -> list[Any]:
                return []  # display-only: never persist

        return ReadOnlyFormSet

    @staticmethod
    def _parent_object_id(request: Any) -> Any:
        if request is None or request.resolver_match is None:
            return None
        return request.resolver_match.kwargs.get("object_id")


__all__ = ["DataProviderAdmin", "ProviderEntityMapAdmin", "ProviderEntityMapInline"]
