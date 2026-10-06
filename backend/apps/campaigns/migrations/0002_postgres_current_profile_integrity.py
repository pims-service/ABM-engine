"""PostgreSQL-only: make the database prove ``Campaign.current_profile`` belongs to the campaign.

Django has no composite foreign keys, so this adds one by hand:
``(campaign.id, campaign.current_profile_id) -> (campaignprofile.campaign_id, campaignprofile.id)``,
deferred to commit like Django's own foreign keys so a campaign and its version 1 can be
inserted together. The target is the unique constraint ``campaigns_profile_campaign_id_unique``
from 0001. A no-op on other databases (SQLite), where the service layer and tests are the check.
"""

from typing import Any

from django.db import migrations

CONSTRAINT = "campaigns_campaign_current_profile_same_campaign"


def add_composite_fk(apps: Any, schema_editor: Any) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        f"ALTER TABLE campaigns_campaign ADD CONSTRAINT {CONSTRAINT} "
        "FOREIGN KEY (id, current_profile_id) "
        "REFERENCES campaigns_campaignprofile (campaign_id, id) "
        "DEFERRABLE INITIALLY DEFERRED"
    )


def drop_composite_fk(apps: Any, schema_editor: Any) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(f"ALTER TABLE campaigns_campaign DROP CONSTRAINT {CONSTRAINT}")


class Migration(migrations.Migration):
    dependencies = [("campaigns", "0001_tenancy_models")]

    operations = [migrations.RunPython(add_composite_fk, drop_composite_fk)]
