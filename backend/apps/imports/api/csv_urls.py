"""Routes of the CSV import API, included by ``apps.core.urls`` under ``/api/v1/``."""

from django.urls import path

from .batches import CampaignImportBatchViewSet, ImportBatchViewSet
from .csv_views import CsvImportViewSet, CsvTemplateView

urlpatterns = [
    path("imports/csv-template/", CsvTemplateView.as_view(), name="imports-csv-template"),
    path(
        "imports/<uuid:batch_id>/",
        ImportBatchViewSet.as_view({"get": "retrieve"}),
        name="import-batch-detail",
    ),
    path(
        "imports/<uuid:batch_id>/rows/",
        ImportBatchViewSet.as_view({"get": "rows"}),
        name="import-batch-rows",
    ),
    path(
        "imports/<uuid:batch_id>/errors.csv",
        ImportBatchViewSet.as_view({"get": "errors_csv"}),
        name="import-batch-errors-csv",
    ),
    path(
        "imports/<uuid:batch_id>/cancel/",
        ImportBatchViewSet.as_view({"post": "cancel"}),
        name="import-batch-cancel",
    ),
    path(
        "campaigns/<uuid:campaign_id>/imports/",
        CampaignImportBatchViewSet.as_view({"get": "list"}),
        name="campaign-imports",
    ),
    path(
        "campaigns/<uuid:campaign_id>/csv-import/preview/",
        CsvImportViewSet.as_view({"post": "preview"}),
        name="campaign-csv-import-preview",
    ),
    path(
        "campaigns/<uuid:campaign_id>/csv-import/",
        CsvImportViewSet.as_view({"post": "create"}),
        name="campaign-csv-import",
    ),
]
