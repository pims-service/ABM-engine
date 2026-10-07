"""The outreach decision rule against the REAL HumanDecision model from the assessments app.

``test_outreach.py`` exercises the rule with stubs. These tests make sure the lazy adapter in
``apps.outreach.decisions`` really works with ``apps.research.models.HumanDecision``.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.outreach import services
from apps.outreach.decisions import latest_human_decision
from apps.outreach.models import MessageStatus
from apps.research import services as research_services
from tests.factories import make_angle, make_company, make_contact, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def company():
    return make_company()


@pytest.fixture
def angle(company):
    return make_angle(company=company)


@pytest.fixture
def contact(company):
    return make_contact(company=company)


def _decide(company, decision):
    return research_services.record_human_decision(company, decision, make_user())


def test_no_decision_means_no_message(company, angle, contact):
    assert latest_human_decision(company) is None
    with pytest.raises(ValidationError):
        services.create_message(angle, contact, "linkedin", "en", "Hello")


@pytest.mark.parametrize("decision", ["hold", "skip"])
def test_hold_and_skip_block_messages(company, angle, contact, decision):
    _decide(company, decision)
    assert latest_human_decision(company) == decision
    with pytest.raises(ValidationError):
        services.create_message(angle, contact, "linkedin", "en", "Hello")


def test_add_allows_message_creation_and_approval(company, angle, contact):
    _decide(company, "add")
    assert latest_human_decision(company) == "add"
    message = services.create_message(angle, contact, "linkedin", "en", "Hello")
    assert message.status == MessageStatus.DRAFT
    approved = services.approve_message(message, make_user())
    assert approved.status == MessageStatus.APPROVED


def test_changing_mind_to_skip_blocks_approval(company, angle, contact):
    _decide(company, "add")
    message = services.create_message(angle, contact, "linkedin", "en", "Hello")
    _decide(company, "skip")  # the latest decision wins
    with pytest.raises(ValidationError):
        services.approve_message(message, make_user())
