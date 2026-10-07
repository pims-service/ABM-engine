"""Generic guards that walk EVERY project model (issue #53).

Nothing here names a model in a decorator: the parametrization comes from the app registry, so a
model added tomorrow is checked tomorrow. See ``registry.py`` for what a new model must add.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.db import models
from django.db.models.deletion import Collector, ProtectedError

from apps.campaigns.models import Campaign, ClientMembership
from apps.companies.models import DataSource
from apps.core.base import (
    ImmutableRecordError,
    TenantMismatchError,
    TenantModel,
    TenantQuerySet,
)
from apps.core.models import Job
from apps.outreach.models import Message
from apps.research.models import AIRecommendation, HumanDecision, ICPAssessment
from tests.invariants.registry import (
    NOT_TENANT_DATA,
    has_append_only_queryset,
    has_tenant_queryset,
    is_append_only,
    is_tenant_model,
    label,
    project_models,
)

ALL = project_models()
TENANT = [m for m in ALL if is_tenant_model(m)]
TENANT_QS = [m for m in ALL if has_tenant_queryset(m)]
APPEND_ONLY = [m for m in ALL if is_append_only(m)]
IDS = label

#: Tenant tables hanging directly off a client (no parent row to copy ``client_id`` from).
#: Adding a model here is a conscious decision: say why it has no ``tenant_parent``.
ROOT_TENANT_MODELS = {
    Campaign,  # a campaign belongs to a client directly
    DataSource,  # a source is registered per client, not per company
    ClientMembership,  # (user, client, role)
    Job,  # a background run names its client explicitly
}

#: Mutable tables that also guard their own history (not ``AppendOnlyModel`` subclasses).
#: Their queryset still blocks bulk update/delete; text columns are immutable in ``save()``.
GUARDED_NOT_APPEND_ONLY: set[type[models.Model]] = {Message}

#: Models where a numeric score would break Brief section 15 ("explain, do not rank").
NO_SCORE_MODELS = [ICPAssessment, AIRecommendation, HumanDecision]
SCORE_WORDS = ("score", "rating", "rank", "confidence", "probability", "likelihood", "grade")
#: Checked on every model. ``rank`` is left out: Contact.rank orders buyers, it does not score.
GLOBAL_SCORE_WORDS = ("score", "rating", "confidence", "probability", "likelihood")
NUMERIC = (models.IntegerField, models.FloatField, models.DecimalField)


def concrete_values(obj: models.Model) -> dict[str, Any]:
    """The row as stored (a fresh read), to prove nothing changed."""
    return dict(type(obj)._base_manager.filter(pk=obj.pk).values().get())


def editable_column(model: type[models.Model]) -> str:
    """A column (attname, e.g. ``body`` or ``angle_id``) to aim an UPDATE at; plain data first."""
    columns = [f for f in model._meta.concrete_fields if not f.primary_key]
    columns.sort(key=lambda f: f.is_relation)
    return columns[0].attname


# ------------------------------------------------------------------ registry completeness


@pytest.mark.parametrize("model", ALL, ids=IDS)
def test_every_model_has_a_builder(model, two_clients) -> None:
    if label(model) in NOT_TENANT_DATA:
        return
    assert label(model) in two_clients.rows_a, (
        f"{label(model)} is a new model: add a row for it to build_client_rows() in "
        "tests/invariants/registry.py so the generic invariants cover it."
    )
    assert type(two_clients.rows_a[label(model)]) is model


def test_models_are_discovered() -> None:
    """Guards against the registry walk silently matching nothing."""
    assert len(ALL) >= 20
    assert {Campaign, Message, ICPAssessment} <= set(ALL)
    assert len(APPEND_ONLY) >= 10


# ------------------------------------------------------------------ tenancy


@pytest.mark.parametrize("model", TENANT, ids=IDS)
def test_tenant_model_has_a_protected_client_path(model) -> None:
    client = model._meta.get_field("client")
    assert isinstance(client, models.ForeignKey)
    assert client.related_model._meta.label == "campaigns.Client"
    assert client.remote_field.on_delete is models.PROTECT
    assert not client.null, "a tenant row must always have a client"
    parent = model.tenant_parent
    if model in ROOT_TENANT_MODELS:
        assert parent is None, f"{label(model)} is listed as a root but has a tenant_parent"
        return
    assert parent, (
        f"{label(model)} has no tenant_parent: set tenant_parent to the FK its client_id is "
        "copied from, or add it to ROOT_TENANT_MODELS with a reason."
    )
    fk = model._meta.get_field(parent)
    assert isinstance(fk, models.ForeignKey)
    assert issubclass(fk.related_model, TenantModel), "the parent must itself carry a client"


@pytest.mark.parametrize("model", TENANT, ids=IDS)
def test_tenant_manager_scopes_by_user(model) -> None:
    assert has_tenant_queryset(model), (
        f"{label(model)}.objects must be built on TenantQuerySet so for_user() can scope it."
    )
    queryset = model._default_manager.all()
    assert isinstance(queryset, TenantQuerySet)
    assert callable(queryset.for_user)
    assert callable(queryset.for_client)


@pytest.mark.parametrize("model", TENANT, ids=IDS)
def test_client_is_copied_from_the_parent(model, two_clients) -> None:
    row = two_clients.rows_a[label(model)]
    assert row.client_id == two_clients.a.pk
    if model.tenant_parent:
        assert getattr(row, model.tenant_parent).client_id == row.client_id


@pytest.mark.parametrize("model", [m for m in TENANT if getattr(m, "tenant_parent", None)], ids=IDS)
def test_a_row_cannot_carry_another_clients_id_than_its_parent(model, two_clients) -> None:
    """Forging ``client_id`` on a child is refused (``TenantMismatchError``), nothing is stored."""
    row = two_clients.rows_a[label(model)]
    values = {f.attname: getattr(row, f.attname) for f in model._meta.concrete_fields}
    values["id"] = type(row.pk)(int=int(row.pk) ^ 1)  # a fresh key; the row never gets saved
    values["client_id"] = two_clients.b.pk
    forged = model(**values)
    with pytest.raises(TenantMismatchError):
        forged.save()
    assert not model._base_manager.filter(pk=forged.pk).exists()


@pytest.mark.parametrize("model", TENANT, ids=IDS)
def test_a_saved_row_cannot_move_to_another_client(model, two_clients) -> None:
    row = two_clients.rows_a[label(model)]
    before = concrete_values(row)
    row.client_id = two_clients.b.pk
    # Append-only tables refuse any second save; the others refuse the move. No change either way.
    with pytest.raises((TenantMismatchError, ImmutableRecordError)):
        row.save()
    assert concrete_values(row) == before


# ------------------------------------------------------------------ append-only


def test_append_only_models_are_found_automatically() -> None:
    """Fails if a model's queryset blocks bulk writes but the model is not append-only (and the
    exception is not documented), or the other way round."""
    for model in ALL:
        if has_append_only_queryset(model) and not is_append_only(model):
            assert model in GUARDED_NOT_APPEND_ONLY, (
                f"{label(model)} uses AppendOnlyQuerySet but not AppendOnlyModel: make it "
                "append-only, or add it to GUARDED_NOT_APPEND_ONLY with a reason."
            )
        if is_append_only(model):
            assert has_append_only_queryset(model), (
                f"{label(model)} is append-only but its manager does not use AppendOnlyQuerySet, "
                "so a bulk .update() would rewrite history."
            )


@pytest.mark.parametrize("model", APPEND_ONLY, ids=IDS)
class TestAppendOnly:
    def test_save_of_an_existing_row_raises(self, model, two_clients) -> None:
        row = two_clients.rows_a[label(model)]
        before = concrete_values(row)
        with pytest.raises(ImmutableRecordError):
            row.save()
        assert concrete_values(row) == before

    def test_instance_delete_raises(self, model, two_clients) -> None:
        row = two_clients.rows_a[label(model)]
        with pytest.raises(ImmutableRecordError):
            row.delete()
        assert model._base_manager.filter(pk=row.pk).exists()

    def test_queryset_update_raises(self, model, two_clients) -> None:
        row = two_clients.rows_a[label(model)]
        before = concrete_values(row)
        column = editable_column(model)
        with pytest.raises(ImmutableRecordError):
            model.objects.filter(pk=row.pk).update(**{column: before[column]})
        with pytest.raises(ImmutableRecordError):
            model.objects.update(**{column: before[column]})
        assert concrete_values(row) == before

    def test_bulk_update_raises(self, model, two_clients) -> None:
        row = two_clients.rows_a[label(model)]
        before = concrete_values(row)
        with pytest.raises(ImmutableRecordError):
            model.objects.bulk_update([row], [editable_column(model)])
        assert concrete_values(row) == before

    def test_queryset_delete_raises(self, model, two_clients) -> None:
        row = two_clients.rows_a[label(model)]
        with pytest.raises(ImmutableRecordError):
            model.objects.filter(pk=row.pk).delete()
        with pytest.raises(ImmutableRecordError):
            model.objects.all().delete()
        assert model._base_manager.filter(pk=row.pk).exists()

    def test_update_or_create_cannot_rewrite_a_row(self, model, two_clients) -> None:
        row = two_clients.rows_a[label(model)]
        before = concrete_values(row)
        column = editable_column(model)
        with pytest.raises(ImmutableRecordError):
            model.objects.update_or_create(pk=row.pk, defaults={column: before[column]})
        assert concrete_values(row) == before


@pytest.mark.parametrize("model", list(GUARDED_NOT_APPEND_ONLY), ids=IDS)
def test_guarded_models_block_bulk_writes_and_delete(model, two_clients) -> None:
    row = two_clients.rows_a[label(model)]
    before = concrete_values(row)
    with pytest.raises(ImmutableRecordError):
        row.delete()
    with pytest.raises(ImmutableRecordError):
        model.objects.filter(pk=row.pk).update(body="rewritten")
    with pytest.raises(ImmutableRecordError):
        model.objects.filter(pk=row.pk).delete()
    row.body = "rewritten"
    with pytest.raises(ImmutableRecordError):
        row.save()
    assert concrete_values(row) == before


# ------------------------------------------------------------------ foreign keys


def foreign_keys() -> list[tuple[type[models.Model], models.ForeignKey]]:
    return [
        (m, f) for m in ALL for f in m._meta.concrete_fields if isinstance(f, models.ForeignKey)
    ]


FKS = foreign_keys()
FK_IDS = [f"{label(m)}.{f.name}" for m, f in FKS]


@pytest.mark.parametrize(("model", "field"), FKS, ids=FK_IDS)
def test_every_foreign_key_is_protect(model, field) -> None:
    """Rows are archived, never cascaded away (ADR 0009). SET_NULL / CASCADE would erase history."""
    assert field.remote_field.on_delete is models.PROTECT, (
        f"{label(model)}.{field.name} must use on_delete=PROTECT"
    )


@pytest.mark.parametrize(("model", "field"), FKS, ids=FK_IDS)
def test_deleting_a_parent_with_children_is_blocked(model, field, two_clients) -> None:
    """The database-level view of PROTECT: collecting the parent for deletion raises."""
    child = two_clients.rows_a.get(label(model))
    parent_id = None if child is None else getattr(child, field.attname)
    if parent_id is None:
        pytest.skip("the sample row leaves this optional relation empty")
    parent = field.related_model._base_manager.get(pk=parent_id)
    with pytest.raises(ProtectedError):
        Collector(using="default").collect([parent])


# ------------------------------------------------------------------ no scores (Brief section 15)


@pytest.mark.parametrize("model", NO_SCORE_MODELS, ids=IDS)
def test_assessment_models_have_no_numeric_or_score_fields(model) -> None:
    for field in model._meta.get_fields():
        assert not isinstance(field, NUMERIC), f"{label(model)}.{field.name} is numeric"
        assert not any(word in field.name for word in SCORE_WORDS), field.name


@pytest.mark.parametrize("model", ALL, ids=IDS)
def test_no_model_has_a_score_like_column(model) -> None:
    for field in model._meta.get_fields():
        assert not any(word in field.name for word in GLOBAL_SCORE_WORDS), (
            f"{label(model)}.{field.name}: the brief asks for explanations, not scores"
        )
