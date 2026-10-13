# Roles and per-client access control

Isolation between clients is enforced in the backend (issue #46), not only in the UI. Two
clients' campaigns, companies and contacts never show up for someone who is not a member of that
client. Design background: [data-model.md](data-model.md#tenancy-how-data-stays-inside-a-client)
and [ADR 0009](adr/0009-data-model-conventions.md).

## Authentication choice

Users sign in with Django accounts and `djangorestframework-simplejwt`, not Supabase Auth
([ADR 0006](adr/0006-django-jwt-authentication.md)): users, clients and memberships live in
one database, so there is no second user store to keep in sync, and the API checks roles
itself. Access tokens last 15 minutes, refresh tokens 7 days and rotate with a blacklist.
Endpoints and settings are in `backend/README.md`, "Authentication". The Django admin is a
separate session login for `is_staff` users; client roles never grant it ([admin.md](admin.md)).

A worked example of a new endpoint for a new record type is in
[data-model.md](data-model.md#how-to-add-a-permission-safe-endpoint).

## Who can do what

There are two kinds of power:

- **Global admin**: `User.is_superuser` (and active). Sees and does everything in every client,
  needs no membership. Meant for the operators of the platform.
- **Client role**: a `ClientMembership` row (user, client, role). The role only applies inside
  that client. The same person can be admin of client A and viewer of client B.

Roles are cumulative: each one can do everything the row above it can.

| Role | Level | Can do |
| --- | --- | --- |
| viewer | READ | see the client's campaigns, companies, contacts, research, decisions |
| reviewer | DECIDE | everything a viewer can, plus record human decisions (approve, reject, defer) |
| manager | EDIT | everything a reviewer can, plus edit campaign rules (new profile version), create and edit campaigns, companies and contacts |
| admin | MANAGE | everything a manager can, plus manage the client's memberships and archive things |

### Matrix

`yes` = allowed, `no` = 403 (the user can see the object but the role is too low), `404` = the
object belongs to a client the user is not a member of, so it does not exist for them.

| Action | Level | viewer | reviewer | manager | admin | global admin | no membership in that client |
| --- | --- | --- | --- | --- | --- | --- | --- |
| list / retrieve | READ | yes | yes | yes | yes | yes | 404 (lists are simply empty of it) |
| record a decision | DECIDE | no | yes | yes | yes | yes | 404 |
| edit campaign rules (new version) | EDIT | no | no | yes | yes | yes | 404 |
| create / edit campaign, company, contact | EDIT | no | no | yes | yes | yes | 404 (create: 404 for a client they cannot see) |
| archive / delete | MANAGE | no | no | no | yes | yes | 404 |
| grant, change or revoke memberships | MANAGE | no | no | no | yes (own client) | yes | 404 |

### Client endpoints (`/api/v1/clients/`, issue #47)

| Action | Level | viewer | reviewer | manager | admin | global admin | no membership in that client |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `list`, `retrieve` | READ | yes | yes | yes | yes | yes | not listed / 404 |
| `partial_update`, `update` (name, notes) | EDIT | no | no | yes | yes | yes | 404 |
| `archive`, `restore` | MANAGE | no | no | no | yes | yes | 404 |
| `create` | MANAGE (in any client) | no | no | no | yes | yes | 403 (no client to hide yet) |

There is no DELETE (405): clients are archived. The creator of a client becomes its admin member.
Archived clients are read-only (400) until restored.

### Campaign endpoints (`/api/v1/campaigns/`, issue #48)

| Action | Level | viewer | reviewer | manager | admin | global admin | no membership in that client |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `list`, `retrieve`, `profile_versions`, `profile_version`, `rules_summary` | READ | yes | yes | yes | yes | yes | not listed / 404 |
| `create` (also `/clients/{id}/campaigns/`) | EDIT in the target client | no | no | yes | yes | yes | 404 (403 if they can only view it or hold no EDIT role anywhere) |
| `partial_update`, `update` (name and/or rules: a rule change makes a new profile version) | EDIT | no | no | yes | yes | yes | 404 |
| `clone`, `activate` | EDIT | no | no | yes | yes | yes | 404 |
| `archive`, `restore` | MANAGE | no | no | no | yes | yes | 404 |

Reviewers (DECIDE) cannot edit rules: PUT/PATCH is 403 for them. There is no DELETE (405).
Archived campaigns are read-only (400) until restored; their profile versions stay readable.

### CSV import endpoints (issue #62)

| Endpoint (action) | Level | viewer | reviewer | manager | admin | global admin | no membership in that client |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `POST /campaigns/{campaign_id}/csv-import/preview/` (`preview`) | EDIT | no | no | yes | yes | yes | 404 |
| `POST /campaigns/{campaign_id}/csv-import/` (`create`) | EDIT | no | no | yes | yes | yes | 404 |
| `POST /imports/{batch_id}/cancel/` (`cancel`) | EDIT | no | no | yes | yes | yes | 404 |
| `GET /imports/{batch_id}/` (`retrieve`), `GET /imports/{batch_id}/rows/` (`rows`), `GET /imports/{batch_id}/errors.csv` (`errors_csv`) | READ | yes | yes | yes | yes | yes | 404 |
| `GET /campaigns/{campaign_id}/imports/` (`list`, import history) | READ | yes | yes | yes | yes | yes | 404 |
| `GET /imports/csv-template/` | signed in | yes | yes | yes | yes | yes | yes (a static file, no client data; listed in `PUBLIC_ENDPOINTS` of the isolation test) |

Importing adds companies to a campaign, so it needs the same level as creating a company.

### Company endpoints (issue #59)

`/api/v1/campaigns/{campaign_id}/companies/` and `/api/v1/companies/{id}/`. The campaign of the
URL (or the company's client) is looked up through the user's visible clients: another client's
campaign or company is 404.

| Action | Level | viewer | reviewer | manager | admin | global admin | no membership in that client |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `list` (`GET campaigns/{id}/companies/`), `retrieve` | READ | yes | yes | yes | yes | yes | 404 |
| `create` (manual entry, `POST campaigns/{id}/companies/`) | EDIT | no | no | yes | yes | yes | 404 |
| `analyze` (`POST companies/{id}/analyze/`) | EDIT | no | no | yes | yes | yes | 404 |
| `archive`, `restore` | MANAGE | no | no | no | yes | yes | 404 |

There is no PUT, PATCH or DELETE (405 for MANAGE, 403 below it: an unrouted write fails closed).
Archived campaigns and clients take no new companies, analysis, archive or restore (400).

### Integration connection endpoints (`/api/v1/integrations/connections/`, issue #76)

Connections hold provider credentials, so they are **stricter than the rest of the client API**:
reading is limited to managers and admins, and only admins change anything. Viewers and reviewers
never see that a connection exists beyond a 403 on a known id.

| Action | Level | viewer | reviewer | manager | admin | global admin | no membership in that client |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `list` | EDIT | 403 | 403 | yes (own clients where manager/admin) | yes | yes | not listed |
| `retrieve` | EDIT | 403 | 403 | yes | yes | yes | 404 |
| `create` | MANAGE in the target client | 403 | 403 | 403 | yes | yes | 404 |
| `update`, `partial_update` (name, config, credentials) | MANAGE | 403 | 403 | 403 | yes | yes | 404 |
| `enable`, `disable`, `archive`, `restore` | MANAGE | 403 | 403 | 403 | yes | yes | 404 |

Credentials are write-only: no response contains them, only `has_credentials` and a masked hint
(`****abcd` for secrets of 16 characters or more, `****` otherwise). There is no DELETE (405):
connections are archived. Not even the admin role can read a secret back; the only reader is
`apps.integrations.services.get_credentials`, used by adapters. See
[backend/README.md](../backend/README.md#integration-credentials-issue-76).

Other rules:

- Anonymous requests get 401. An inactive user (or inactive global admin) gets nothing, even with
  a valid token or a membership.
- Revoking a membership or changing a role applies on the very next request (nothing is cached
  between requests).
- A write action nobody declared a level for needs MANAGE; reads need READ. Forgetting to list an
  action therefore fails closed.
- Creating something needs an EDIT role in the **target** client, which is looked up through the
  user's visible clients (`resolve_client`): another client's id gives 404, a client you can only
  view gives 403.
- Membership changes are written to the audit log (`grant_membership`, `change_role` and
  `revoke_membership` call `record_change`).

## How it works

| Piece | Where | What it does |
| --- | --- | --- |
| `ClientMembership` | `apps/campaigns/models.py` | user, client, role. Unique per (user, client). Revoking archives the row (`archived_at`), granting again restores it. Only non-archived rows give access. |
| `Role`, `Level`, `role_allows` | `apps/core/roles.py` | the roles and the matrix above as code. |
| `accessible_client_ids(user)` | `apps/core/tenancy.py` | `None` for a global admin, else the client ids of the user's active memberships, `set()` for anonymous or inactive users. |
| `TenantQuerySet.for_user(user)` | `apps/core/base.py` | filters any tenant model by those ids. |
| `ClientRolePermission`, `ClientScopedMixin`, `ClientScoped*ViewSet` | `apps/core/permissions.py` | the DRF layer. |
| `grant_membership`, `change_role`, `revoke_membership` | `apps/campaigns/memberships.py` | the only write path for memberships. |

Memberships are managed in the Django admin (`Client memberships`, superusers only) or through
the services.

## How to protect a new endpoint

Every endpoint that returns or changes client data MUST be built on these classes. Do not write
your own permission logic and do not query tenant models with a bare `Model.objects.all()`.

1. Make sure the model is a `TenantModel` with a `TenantQuerySet` manager (see
   `backend/README.md`, "Domain model building blocks").
2. Subclass `ClientScopedModelViewSet` (CRUD), `ClientScopedReadOnlyModelViewSet` (read only) or
   `ClientScopedViewSet` (pick your own mixins) from `apps.core.permissions`.
3. Set `queryset = Model.objects.all()` (it is scoped with `for_user` for you; it raises
   `ImproperlyConfigured` if the manager cannot scope). If you override `get_queryset`, call
   `super().get_queryset()` first and only narrow it further.
4. Declare `action_levels = {"action": Level.X, ...}` for every write action and every custom
   `@action`. Use `Level.DECIDE` for decisions, `Level.EDIT` for rules, companies and contacts,
   `Level.MANAGE` for archive and membership management.
5. For `get_object()` nothing more is needed: it uses the scoped queryset (404 for other clients)
   and then runs the role check.
6. When the client comes from the request body or URL (create, move, nested routes), resolve it
   with `self.resolve_client(client_id, Level.EDIT)` and never trust it directly.
7. Write the tests: one per role against each action, an id from another client (expect 404), an
   inactive user and a list that must not leak. Copy `backend/tests/test_permissions.py`.

```python
from apps.core.permissions import ClientScopedModelViewSet
from apps.core.roles import Level


class CampaignViewSet(ClientScopedModelViewSet):
    queryset = Campaign.objects.all()
    serializer_class = CampaignSerializer
    action_levels = {
        "create": Level.EDIT,
        "partial_update": Level.EDIT,
        "rules": Level.EDIT,
        "decide": Level.DECIDE,
        "destroy": Level.MANAGE,
    }

    def perform_create(self, serializer):
        client = self.resolve_client(self.request.data.get("client"), Level.EDIT)
        serializer.save(client=client)
```

A complete working example (with custom actions and the client lookup) is
`backend/tests/permissions_demo.py`; `backend/tests/test_permissions.py` exercises it for every
role.

For background jobs, there is no request user: load data with `Model.objects.for_client(client)`
using the client id the job carries.

## Changing the rules

If a role gains or loses a power, change `apps/core/roles.py`, this page, and the role x action
tests together. A new role needs a migration (the role column has a database check constraint).
