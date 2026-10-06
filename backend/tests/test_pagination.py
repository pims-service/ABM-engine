from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from apps.core.pagination import DefaultPagination


def _paginate(query: str) -> list[int]:
    request = Request(APIRequestFactory().get(f"/?{query}"))
    return DefaultPagination().paginate_queryset(list(range(500)), request) or []


def test_default_page_size_param_is_honoured():
    assert len(_paginate("page_size=7")) == 7


def test_page_size_is_capped():
    assert len(_paginate("page_size=1000")) == 100
