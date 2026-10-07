# Django admin

The admin at `/admin/` is the developers' and operators' window onto the data layer (issue #54).
It is for inspecting records and the few safe edits listed below; the product itself runs through
the API. The code is in each app's `admin.py` and the shared helpers in
`backend/apps/core/admin_base.py`; the rules are enforced by `backend/tests/test_admin.py`.

## Who can use it

- **Only `User.is_staff`** can sign in (Django's `AdminSite.has_permission`, plus `is_active`).
  Sign-in is Django's session login, separate from the API's JWT.
- **Client roles do not grant admin access.** A `ClientMembership` with role `admin` is an admin
  *inside the product* for one client; it does not make the person staff. A superuser without
  `is_staff` cannot sign in either.
- Staff see only the models their Django model permissions allow (granted through groups or user
  permissions). A staff user with no permissions gets an empty index.
- **Superusers** see and may do everything the admin allows. The superuser flag is also the
  *global admin* of the API ([permissions.md](permissions.md)).
- Some models are **superuser only** even if a staff user is granted their permissions, because
  they control access or hold credentials: users, groups, client memberships, the token
  tables, Django-Q task tables and the admin log (`SuperuserOnlyMixin`).

The admin is not tenant-scoped: staff see every client's rows. Give the staff flag only to people
who may see all clients.

## What each model allows

| Behaviour | Models | How |
| --- | --- | --- |
| **View only** (no add, change or delete for anyone, superusers included) | every append-only model: `CampaignProfile`, `CompanyResearch`, `DataSource`, `Signal`, `ICPAssessment`, `AIRecommendation`, `HumanDecision`, `OutreachAngle`, `AngleSignal`, `AngleSource`, `MessageSignal`, `MessageSource`, `Activity`, `AuditLog`; plus `Job`, `JobItem`, `Message`, and the token, task and log tables | `ReadOnlyAdmin` |
| **View, plus archive and restore actions** | `Company`, `Contact` | created and edited through services (`create_company`, `create_contact`, `update_contact`), which keep dedupe and the primary/secondary rules |
| **Edit through services, no delete** | `Client` (name, notes), `Campaign` (name; created through `create_campaign`), `ClientMembership` (role; superusers only) | `save_model` calls the service, so the change is audited with the acting user |
| **Edit** | `User` (superusers only), `Group` (superusers only) | Django's own forms, minus the password hash |

There is no delete button on any domain model: rows are archived, and history is never removed.
`delete_selected` is therefore never offered. Hard delete for privacy requests is not an admin
feature ([data-model.md](data-model.md#archive-and-delete-rules)).

Every changelist has useful `list_display`, `list_filter`, `search_fields`, a `date_hierarchy` for
time-ordered models and `list_select_related` for every foreign key it shows, so a page costs a
constant number of queries however many rows it lists. `tests/test_admin.py` checks this for 20
changelists.

## Sensitive data

| What | Rule |
| --- | --- |
| User password hash | Never shown, not even Django's truncated summary. The form says whether a usable password is set and links to the stock change-password form. |
| Refresh tokens | `OutstandingToken` hides the `token` string (a live credential). The blacklist is view only: deleting a row would un-revoke a token. Superusers only. |
| Contact email and profile URL | Personal data. **Superusers** see them. **Other staff** see a masked email (`j***@example.com`), `(hidden)` for the profile URL, and cannot search by email (that would reveal it one guess at a time). Name and title stay visible. |
| AuditLog `before` / `after` | Redacted when written (`apps.core.audit`) and again when shown, so a secret that got in by other means never renders. The stored row is never changed. |
| Django-Q tasks | Hold function arguments and results: superusers only, view only, and the stock "resubmit to queue" action is removed. Queued payloads (`OrmQ`) are not shown at all. |
| Sessions | `django.contrib.sessions` is not shown: session keys are credentials. |

Viewing personal data in the admin is not logged. If that becomes a requirement, add it to the
retention and privacy work (open decision 14 in the data model).

## Models that are not in the admin

`ADMIN_EXCLUDED_MODELS` in `apps/core/admin_base.py` is the complete list, each with a reason:
`auth.Permission`, `contenttypes.ContentType`, `sessions.Session`, `django_q.Task` (its
`Success`/`Failure` proxies are registered) and `django_q.OrmQ`. `tests/test_admin.py` walks the
app registry and fails for any concrete model that is neither registered nor listed there, so a
new model cannot be forgotten.

## Adding a model to the admin

1. Register it in the app's `admin.py`. Append-only or service-owned: subclass `ReadOnlyAdmin`.
   Otherwise write `save_model` / actions that call the service layer, never raw `save()`.
2. Set `list_display`, `list_filter`, `search_fields`, `date_hierarchy` and
   `list_select_related` for every foreign key in `list_display`.
3. Personal data: hide or mask it for non-superusers as `ContactAdmin` does. Credentials: leave
   the field out and make the model superuser only (`SuperuserOnlyMixin`).
4. Add a row to `CHANGELISTS` in `tests/test_admin.py` (it creates rows with a factory and
   checks the query count), and a masking test if you hid something.
5. A model that should not be there goes in `ADMIN_EXCLUDED_MODELS` with a reason.

## Creating the first admin

`python manage.py createsuperuser`, or the dev seed (`DEV_SUPERUSER_EMAIL` and
`DEV_SUPERUSER_PASSWORD`, see [onboarding.md](onboarding.md)). Other staff: create the user as a
superuser in the admin, tick **Staff status**, and grant model permissions through a group.
