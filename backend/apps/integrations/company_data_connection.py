"""The client's company-data connection as a ready adapter (issue #93, Brief section 12).

Same idea as ``people_connection``: this module is the one place that decides which adapter the
research collectors talk to. No real company-data provider is built in yet, so the only adapter
is the offline fake, named by ``settings.RESEARCH_DEV_COMPANY_ADAPTER`` (a dotted path, set in
the dev and test settings only; production code never imports the fakes). A client
with a connection for a provider we have no adapter for gets a clear, safe message instead of a
silent fake. Real adapters join here when they exist (see docs/research/research-engine.md).
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.utils.module_loading import import_string

from apps.integrations.services import resolve_connection

COMPANY_DATA = "company_data"


class CompanyDataUnavailable(Exception):
    """No usable company-data source. The message is safe to show."""


def get_adapter(client_id: Any) -> Any:
    """The company-data adapter for ``client_id``, or ``CompanyDataUnavailable``."""
    connection = resolve_connection(client_id, COMPANY_DATA)
    if connection is not None:
        raise CompanyDataUnavailable(
            f"The provider '{connection.provider}' is not supported yet for company data."
        )
    dev_adapter = getattr(settings, "RESEARCH_DEV_COMPANY_ADAPTER", "")
    if dev_adapter:
        return import_string(dev_adapter)()
    raise CompanyDataUnavailable(
        "No company data provider is connected for this client, so the profile and hiring "
        "signals cannot be collected."
    )
