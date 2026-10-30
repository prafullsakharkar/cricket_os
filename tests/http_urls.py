"""Test URLconf exercising CacheControlMixin with and without per-view TTLs."""

from __future__ import annotations

from django.urls import path
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.http import CacheControlMixin


class CachedView(CacheControlMixin, APIView):
    cache_max_age = 300
    cache_swr = 600

    def get(self, request):
        return Response({"data": {"value": 1}})


class UncachedView(CacheControlMixin, APIView):
    def get(self, request):
        return Response({"data": {"value": 1}})


urlpatterns = [
    path("cached/", CachedView.as_view(), name="cached"),
    path("uncached/", UncachedView.as_view(), name="uncached"),
]
