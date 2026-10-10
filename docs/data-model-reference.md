# Data model field reference

Generated from the Django models by `python manage.py print_schema --write` (run it from
`backend/`). Do not edit the block below by hand: `backend/tests/test_data_model_docs.py` fails
when it no longer matches the code. For what the entities mean, the history rules and the
conventions, read [data-model.md](data-model.md).

Types are the PostgreSQL types (`text[]` is a JSON list in `text` on SQLite). `Null` is whether
the column accepts NULL. Constraint and index names are the real database names.

<!-- BEGIN GENERATED: reference (python manage.py print_schema --write) -->
### User

`accounts.User`, mutable, table `accounts_user`.

| Field | Type | Null | Notes |
| --- | --- | --- | --- |
| password | text(128) | no |  |
| last_login | timestamptz | yes |  |
| is_superuser | bool | no |  |
| id | uuid | no | PK |
| email | text(254) | no | unique |
| name | text(150) | no |  |
| is_active | bool | no |  |
| is_staff | bool | no |  |
| created_at | timestamptz | no |  |
| updated_at | timestamptz | no |  |

Constraints:

- `accounts_user_email_lowercase`: check

### Campaign

`campaigns.Campaign`, mutable, archivable, tenant, table `campaigns_campaign`.

| Field | Type | Null | Notes |
| --- | --- | --- | --- |
| id | uuid | no | PK |
| created_at | timestamptz | no |  |
| updated_at | timestamptz | no |  |
| archived_at | timestamptz | yes |  |
| client_id | uuid | no | FK to campaigns.Client |
| name | text(200) | no |  |
| status | text(16) | no | one of: `draft`, `active`, `archived` |
| current_profile_id | uuid | no | FK to campaigns.CampaignProfile |
| created_by_id | uuid | yes | FK to accounts.User |

Constraints:

- `campaigns_campaign_archived_matches_status`: check
- `campaigns_campaign_name_unique_active`: unique (client, Lower(name)) (partial)
- `campaigns_campaign_status_valid`: check

### CampaignProfile

`campaigns.CampaignProfile`, append-only, tenant, table `campaigns_campaignprofile`.

| Field | Type | Null | Notes |
| --- | --- | --- | --- |
| id | uuid | no | PK |
| client_id | uuid | no | FK to campaigns.Client |
| campaign_id | uuid | no | FK to campaigns.Campaign |
| version | int | no |  |
| offer | text | no |  |
| countries | text[] | no |  |
| industries | text[] | no |  |
| company_size_min | int | yes |  |
| company_size_max | int | yes |  |
| business_model | text(8) | no | one of: `b2b`, `b2c`, `both` |
| excluded_industries | text[] | no |  |
| excluded_company_types | text[] | no |  |
| target_departments | text[] | no |  |
| preferred_buyer_titles | text[] | no |  |
| outreach_languages | text[] | no |  |
| custom_rules | text | no |  |
| change_note | text | no |  |
| created_by_id | uuid | yes | FK to accounts.User |
| created_at | timestamptz | no |  |

Constraints:

- `campaigns_profile_business_model_valid`: check
- `campaigns_profile_campaign_id_unique`: unique (campaign, id)
- `campaigns_profile_offer_not_empty`: check
- `campaigns_profile_size_min_lte_max`: check
- `campaigns_profile_version_positive`: check
- `campaigns_profile_version_unique`: unique (campaign, version)

### Client

`campaigns.Client`, mutable, archivable, table `campaigns_client`.

| Field | Type | Null | Notes |
| --- | --- | --- | --- |
| id | uuid | no | PK |
| created_at | timestamptz | no |  |
| updated_at | timestamptz | no |  |
| archived_at | timestamptz | yes |  |
| name | text(200) | no |  |
| notes | text | no |  |
| status | text(16) | no | one of: `active`, `archived` |
| created_by_id | uuid | yes | FK to accounts.User |

Constraints:

- `campaigns_client_archived_matches_status`: check
- `campaigns_client_name_unique_active`: unique (Lower(name)) (partial)
- `campaigns_client_status_valid`: check

### ClientMembership

`campaigns.ClientMembership`, mutable, archivable, tenant, table `campaigns_clientmembership`.

| Field | Type | Null | Notes |
| --- | --- | --- | --- |
| id | uuid | no | PK |
| created_at | timestamptz | no |  |
| updated_at | timestamptz | no |  |
| archived_at | timestamptz | yes |  |
| client_id | uuid | no | FK to campaigns.Client |
| user_id | uuid | no | FK to accounts.User |
| role | text(16) | no | one of: `admin`, `manager`, `reviewer`, `viewer` |

Constraints:

- `campaigns_membership_role_valid`: check
- `campaigns_membership_user_client_unique`: unique (user, client)

### Company

`companies.Company`, mutable, archivable, tenant, table `companies_company`.

| Field | Type | Null | Notes |
| --- | --- | --- | --- |
| id | uuid | no | PK |
| created_at | timestamptz | no |  |
| updated_at | timestamptz | no |  |
| archived_at | timestamptz | yes |  |
| client_id | uuid | no | FK to campaigns.Client |
| campaign_id | uuid | no | FK to campaigns.Campaign |
| name | text(300) | no |  |
| website | text(2000) | no |  |
| domain | text(253) | yes |  |
| profile_url | text(2000) | no |  |
| country | text(2) | no |  |
| input_source | text(16) | no | one of: `manual`, `csv`, `provider` |
| status | text(16) | no | one of: `pending`, `analyzing`, `analyzed`, `failed` |
| created_by_job_id | uuid | yes |  |
| created_by_id | uuid | yes | FK to accounts.User |

Constraints:

- `companies_company_domain_unique_per_campaign`: unique (campaign, domain) (partial)
- `companies_company_input_source_valid`: check
- `companies_company_name_not_empty`: check
- `companies_company_status_valid`: check

Indexes:

- `co_company_camp_arch_idx`: (campaign, archived_at)
- `co_company_camp_lname_idx`: (Lower(name), campaign)
- `co_company_camp_status_idx`: (campaign, status)
- `co_company_client_domain_idx`: (client, domain)

### CompanyResearch

`companies.CompanyResearch`, append-only, tenant, table `companies_companyresearch`.

| Field | Type | Null | Notes |
| --- | --- | --- | --- |
| id | uuid | no | PK |
| client_id | uuid | no | FK to campaigns.Client |
| company_id | uuid | no | FK to companies.Company |
| data_source_id | uuid | no | FK to companies.DataSource |
| researched_at | timestamptz | no |  |
| industry | text(200) | yes |  |
| description | text | yes |  |
| headquarters | text(300) | yes |  |
| employee_count | int | yes |  |
| business_model | text | yes |  |
| classification | text(8) | yes | one of: `b2b`, `b2c`, `both`, `unknown` |
| products_services | text | yes |  |
| target_customers | text | yes |  |
| sales_headcount | int | yes |  |
| bd_headcount | int | yes |  |
| marketing_headcount | int | yes |  |
| commercial_partnerships_headcount | int | yes |  |
| department_growth | jsonb | yes |  |
| headcount_change_3m | numeric(6,2) | yes |  |
| headcount_change_6m | numeric(6,2) | yes |  |
| headcount_change_12m | numeric(6,2) | yes |  |
| created_at | timestamptz | no |  |

Constraints:

- `companies_research_classification_valid`: check

Indexes:

- `co_research_client_idx`: (client)
- `co_research_latest_idx`: (company, -researched_at, -created_at, -id)
- `co_research_source_idx`: (data_source)

### Contact

`companies.Contact`, mutable, archivable, tenant, table `companies_contact`.

| Field | Type | Null | Notes |
| --- | --- | --- | --- |
| id | uuid | no | PK |
| created_at | timestamptz | no |  |
| updated_at | timestamptz | no |  |
| archived_at | timestamptz | yes |  |
| client_id | uuid | no | FK to campaigns.Client |
| company_id | uuid | no | FK to companies.Company |
| data_source_id | uuid | no | FK to companies.DataSource |
| name | text(300) | no |  |
| title | text(300) | no |  |
| profile_url | text(2000) | no |  |
| email | text(320) | no |  |
| email_status | text(16) | no | one of: `unknown`, `not_found`, `unverified`, `verified`, `invalid` |
| relevance_reason | text | no |  |
| rank | int | yes |  |
| role | text(16) | no | one of: `primary`, `secondary`, `none` |
| erased_at | timestamptz | yes |  |
| created_by_id | uuid | yes | FK to accounts.User |

Constraints:

- `companies_contact_email_status_valid`: check
- `companies_contact_name_not_empty`: check
- `companies_contact_not_found_no_email`: check
- `companies_contact_one_primary_secondary`: unique (company, role) (partial)
- `companies_contact_profile_url_unique`: unique (company, profile_url) (partial)
- `companies_contact_rank_positive`: check
- `companies_contact_role_valid`: check
- `companies_contact_status_needs_email`: check

Indexes:

- `co_contact_client_idx`: (client)
- `co_contact_company_arch_idx`: (company, archived_at)
- `co_contact_source_idx`: (data_source)

### DataSource

`companies.DataSource`, append-only, tenant, table `companies_datasource`.

| Field | Type | Null | Notes |
| --- | --- | --- | --- |
| id | uuid | no | PK |
| client_id | uuid | no | FK to campaigns.Client |
| type | text(16) | no | one of: `provider`, `website`, `news`, `manual` |
| name | text(200) | no |  |
| url | text(2000) | no |  |
| provider_reference | text(200) | no |  |
| retrieved_at | timestamptz | no |  |
| evidence_date | date | yes |  |
| created_by_id | uuid | yes | FK to accounts.User |
| created_at | timestamptz | no |  |

Constraints:

- `companies_datasource_type_valid`: check
- `companies_datasource_url_required`: check

Indexes:

- `co_ds_client_retrieved_idx`: (client, -retrieved_at)

### AuditLog

`core.AuditLog`, append-only, table `core_auditlog`.

| Field | Type | Null | Notes |
| --- | --- | --- | --- |
| id | uuid | no | PK |
| actor_id | uuid | yes | FK to accounts.User |
| client_id | uuid | yes | FK to campaigns.Client |
| action | text(16) | no | one of: `create`, `update`, `archive`, `restore`, `delete` |
| object_type | text(64) | no |  |
| object_id | uuid | no |  |
| before | jsonb | yes |  |
| after | jsonb | yes |  |
| request_id | text(64) | no |  |
| created_at | timestamptz | no |  |

Constraints:

- `core_auditlog_action_valid`: check

Indexes:

- `core_audit_client`: (client, -created_at)
- `core_audit_object`: (object_type, object_id, -created_at)

### Job

`core.Job`, mutable, tenant, table `core_job`.

| Field | Type | Null | Notes |
| --- | --- | --- | --- |
| id | uuid | no | PK |
| created_at | timestamptz | no |  |
| updated_at | timestamptz | no |  |
| client_id | uuid | no | FK to campaigns.Client |
| type | text(100) | no |  |
| campaign_id | uuid | yes | FK to campaigns.Campaign |
| status | text(16) | no | one of: `queued`, `running`, `succeeded`, `partial`, `failed` |
| total_count | int | no |  |
| done_count | int | no |  |
| failed_count | int | no |  |
| attempts | smallint | no |  |
| error_summary | text | no |  |
| queue_task_id | text(64) | no |  |
| created_by_id | uuid | yes | FK to accounts.User |
| started_at | timestamptz | yes |  |
| finished_at | timestamptz | yes |  |

Constraints:

- `core_job_counts_within_total`: check
- `core_job_finished_matches_status`: check
- `core_job_status_valid`: check

Indexes:

- `core_job_campaign_created`: (campaign, -created_at)
- `core_job_client_created`: (client, -created_at)
- `core_job_status`: (status)

### JobItem

`core.JobItem`, mutable, tenant, table `core_jobitem`.

| Field | Type | Null | Notes |
| --- | --- | --- | --- |
| id | uuid | no | PK |
| created_at | timestamptz | no |  |
| updated_at | timestamptz | no |  |
| client_id | uuid | no | FK to campaigns.Client |
| job_id | uuid | no | FK to core.Job |
| subject_type | text(64) | no |  |
| subject_id | uuid | no |  |
| status | text(16) | no | one of: `queued`, `running`, `succeeded`, `failed` |
| error | text | no |  |
| result | jsonb | yes |  |
| started_at | timestamptz | yes |  |
| finished_at | timestamptz | yes |  |

Constraints:

- `core_jobitem_finished_matches_status`: check
- `core_jobitem_status_valid`: check
- `core_jobitem_subject_unique`: unique (job, subject_type, subject_id)

Indexes:

- `core_jobitem_job_status`: (job, status)

### ImportBatch

`imports.ImportBatch`, mutable, tenant, table `imports_importbatch`.

| Field | Type | Null | Notes |
| --- | --- | --- | --- |
| id | uuid | no | PK |
| created_at | timestamptz | no |  |
| updated_at | timestamptz | no |  |
| client_id | uuid | no | FK to campaigns.Client |
| campaign_id | uuid | no | FK to campaigns.Campaign |
| source | text(16) | no | one of: `manual`, `csv`, `provider` |
| status | text(16) | no | one of: `pending`, `processing`, `completed`, `partial`, `failed`, `cancelled` |
| original_filename | text(255) | no |  |
| file_size | PositiveBigIntegerField | yes |  |
| file_sha256 | text(64) | no |  |
| column_mapping | jsonb | no |  |
| total_count | int | no |  |
| created_count | int | no |  |
| duplicate_count | int | no |  |
| restored_count | int | no |  |
| skipped_count | int | no |  |
| failed_count | int | no |  |
| error_summary | text | no |  |
| created_by_id | uuid | yes | FK to accounts.User |
| job_id | uuid | yes | FK to core.Job |
| started_at | timestamptz | yes |  |
| finished_at | timestamptz | yes |  |

Constraints:

- `imports_batch_counts_within_total`: check
- `imports_batch_csv_has_sha256`: check
- `imports_batch_finished_matches_status`: check
- `imports_batch_source_valid`: check
- `imports_batch_status_valid`: check

Indexes:

- `imp_batch_campaign_created`: (campaign, -created_at)
- `imp_batch_campaign_sha`: (campaign, file_sha256)
- `imp_batch_client_created`: (client, -created_at)
- `imp_batch_status`: (status)

### ImportRow

`imports.ImportRow`, append-only, tenant, table `imports_importrow`.

| Field | Type | Null | Notes |
| --- | --- | --- | --- |
| id | uuid | no | PK |
| client_id | uuid | no | FK to campaigns.Client |
| batch_id | uuid | no | FK to imports.ImportBatch |
| row_number | int | no |  |
| raw_data | jsonb | no |  |
| name | text(300) | no |  |
| website | text(2000) | no |  |
| domain | text(253) | yes |  |
| profile_url | text(2000) | no |  |
| country | text(2) | no |  |
| outcome | text(16) | no | one of: `created`, `duplicate`, `restored`, `skipped`, `failed` |
| error_code | text(64) | no |  |
| error_message | text | no |  |
| company_id | uuid | yes | FK to companies.Company |
| created_at | timestamptz | no |  |

Constraints:

- `imports_row_company_for_outcome`: check
- `imports_row_failed_has_code`: check
- `imports_row_number_positive`: check
- `imports_row_number_unique_per_batch`: unique (batch, row_number)
- `imports_row_outcome_valid`: check

Indexes:

- `imp_row_batch_outcome`: (batch, outcome, row_number)
- `imp_row_client`: (client)
- `imp_row_company`: (company)

### Activity

`outreach.Activity`, append-only, tenant, table `outreach_activity`.

| Field | Type | Null | Notes |
| --- | --- | --- | --- |
| id | uuid | no | PK |
| client_id | uuid | no | FK to campaigns.Client |
| company_id | uuid | no | FK to companies.Company |
| campaign_id | uuid | no | FK to campaigns.Campaign |
| type | text(24) | no | one of: `researched`, `recommended`, `decided`, `contacted`, `replied`, `meeting_booked`, `exported`, `feedback` |
| actor_id | uuid | yes | FK to accounts.User |
| payload | jsonb | no |  |
| created_at | timestamptz | no |  |

Constraints:

- `outreach_activity_type_valid`: check

Indexes:

- `out_activity_campaign_idx`: (campaign, type, -created_at)
- `out_activity_client_idx`: (client)
- `out_activity_company_idx`: (company, -created_at)

### AngleSignal

`outreach.AngleSignal`, append-only, tenant, table `outreach_angle_signals`.

| Field | Type | Null | Notes |
| --- | --- | --- | --- |
| id | uuid | no | PK |
| client_id | uuid | no | FK to campaigns.Client |
| angle_id | uuid | no | FK to outreach.OutreachAngle |
| signal_id | uuid | no | FK to research.Signal |

Constraints:

- `outreach_anglesignal_unique`: unique (angle, signal)

Indexes:

- `out_anglesig_signal_idx`: (signal)

### AngleSource

`outreach.AngleSource`, append-only, tenant, table `outreach_angle_sources`.

| Field | Type | Null | Notes |
| --- | --- | --- | --- |
| id | uuid | no | PK |
| client_id | uuid | no | FK to campaigns.Client |
| angle_id | uuid | no | FK to outreach.OutreachAngle |
| data_source_id | uuid | no | FK to companies.DataSource |

Constraints:

- `outreach_anglesource_unique`: unique (angle, data_source)

Indexes:

- `out_anglesrc_source_idx`: (data_source)

### Message

`outreach.Message`, mutable, tenant, table `outreach_message`.

| Field | Type | Null | Notes |
| --- | --- | --- | --- |
| id | uuid | no | PK |
| client_id | uuid | no | FK to campaigns.Client |
| company_id | uuid | no | FK to companies.Company |
| contact_id | uuid | no | FK to companies.Contact |
| angle_id | uuid | no | FK to outreach.OutreachAngle |
| channel | text(16) | no | one of: `linkedin`, `email`, `whatsapp`, `call` |
| language | text(16) | no |  |
| subject | text(300) | no |  |
| body | text | no |  |
| is_followup | bool | no |  |
| status | text(16) | no | one of: `draft`, `approved`, `exported` |
| approved_by_id | uuid | yes | FK to accounts.User |
| approved_at | timestamptz | yes |  |
| exported_at | timestamptz | yes |  |
| supersedes_id | uuid | yes | FK to outreach.Message; unique |
| model_name | text(100) | no |  |
| prompt_version | text(50) | no |  |
| schema_version | text(50) | no |  |
| created_by_id | uuid | yes | FK to accounts.User |
| created_at | timestamptz | no |  |
| signals | many to many | n/a | through `outreach.MessageSignal` |
| data_sources | many to many | n/a | through `outreach.MessageSource` |

Constraints:

- `outreach_message_approved_needs_approver`: check
- `outreach_message_approved_needs_time`: check
- `outreach_message_body_not_empty`: check
- `outreach_message_channel_valid`: check
- `outreach_message_exported_needs_time`: check
- `outreach_message_language_not_empty`: check
- `outreach_message_status_valid`: check
- `outreach_message_subject_email_only`: check

Indexes:

- `out_message_angle_idx`: (angle)
- `out_message_client_idx`: (client)
- `out_message_company_status_idx`: (company, status)
- `out_message_contact_idx`: (contact)

### MessageSignal

`outreach.MessageSignal`, append-only, tenant, table `outreach_message_signals`.

| Field | Type | Null | Notes |
| --- | --- | --- | --- |
| id | uuid | no | PK |
| client_id | uuid | no | FK to campaigns.Client |
| message_id | uuid | no | FK to outreach.Message |
| signal_id | uuid | no | FK to research.Signal |

Constraints:

- `outreach_messagesignal_unique`: unique (message, signal)

Indexes:

- `out_msgsig_signal_idx`: (signal)

### MessageSource

`outreach.MessageSource`, append-only, tenant, table `outreach_message_sources`.

| Field | Type | Null | Notes |
| --- | --- | --- | --- |
| id | uuid | no | PK |
| client_id | uuid | no | FK to campaigns.Client |
| message_id | uuid | no | FK to outreach.Message |
| data_source_id | uuid | no | FK to companies.DataSource |

Constraints:

- `outreach_messagesource_unique`: unique (message, data_source)

Indexes:

- `out_msgsrc_source_idx`: (data_source)

### OutreachAngle

`outreach.OutreachAngle`, append-only, tenant, table `outreach_outreachangle`.

| Field | Type | Null | Notes |
| --- | --- | --- | --- |
| id | uuid | no | PK |
| client_id | uuid | no | FK to campaigns.Client |
| company_id | uuid | no | FK to companies.Company |
| angle | text | no |  |
| rationale | text | no |  |
| model_name | text(100) | no |  |
| prompt_version | text(50) | no |  |
| schema_version | text(50) | no |  |
| created_by_id | uuid | yes | FK to accounts.User |
| created_at | timestamptz | no |  |
| signals | many to many | n/a | through `outreach.AngleSignal` |
| data_sources | many to many | n/a | through `outreach.AngleSource` |

Constraints:

- `outreach_angle_rationale_not_empty`: check
- `outreach_angle_text_not_empty`: check

Indexes:

- `out_angle_client_idx`: (client)
- `out_angle_company_idx`: (company, -created_at)

### AIRecommendation

`research.AIRecommendation`, append-only, tenant, table `research_airecommendation`.

| Field | Type | Null | Notes |
| --- | --- | --- | --- |
| id | uuid | no | PK |
| client_id | uuid | no | FK to campaigns.Client |
| company_id | uuid | no | FK to companies.Company |
| icp_assessment_id | uuid | no | FK to research.ICPAssessment |
| status | text(8) | no | one of: `add`, `hold`, `skip` |
| explanation | text | no |  |
| raw_output | jsonb | no |  |
| model_name | text(100) | no |  |
| prompt_version | text(50) | no |  |
| schema_version | text(50) | no |  |
| created_at | timestamptz | no |  |

Constraints:

- `research_airecommendation_explained`: check
- `research_airecommendation_status_valid`: check
- `research_airecommendation_versions_set`: check

Indexes:

- `rs_airec_assessment_idx`: (icp_assessment)
- `rs_airec_client_idx`: (client)
- `rs_airec_latest_idx`: (company, -created_at, -id)

### HumanDecision

`research.HumanDecision`, append-only, tenant, table `research_humandecision`.

| Field | Type | Null | Notes |
| --- | --- | --- | --- |
| id | uuid | no | PK |
| client_id | uuid | no | FK to campaigns.Client |
| company_id | uuid | no | FK to companies.Company |
| ai_recommendation_id | uuid | yes | FK to research.AIRecommendation |
| decision | text(8) | no | one of: `add`, `hold`, `skip` |
| decided_by_id | uuid | no | FK to accounts.User |
| note | text | no |  |
| decided_at | timestamptz | no |  |
| created_at | timestamptz | no |  |

Constraints:

- `research_humandecision_decision_valid`: check

Indexes:

- `rs_decision_airec_idx`: (ai_recommendation)
- `rs_decision_client_idx`: (client)
- `rs_decision_latest_idx`: (company, -decided_at, -created_at, -id)

### ICPAssessment

`research.ICPAssessment`, append-only, tenant, table `research_icpassessment`.

| Field | Type | Null | Notes |
| --- | --- | --- | --- |
| id | uuid | no | PK |
| client_id | uuid | no | FK to campaigns.Client |
| company_id | uuid | no | FK to companies.Company |
| campaign_profile_id | uuid | no | FK to campaigns.CampaignProfile |
| company_research_id | uuid | no | FK to companies.CompanyResearch |
| fit | text(8) | no | one of: `strong`, `medium`, `weak` |
| reasons | text[] | no |  |
| concerns | text[] | no |  |
| raw_output | jsonb | no |  |
| model_name | text(100) | no |  |
| prompt_version | text(50) | no |  |
| schema_version | text(50) | no |  |
| created_at | timestamptz | no |  |

Constraints:

- `research_icpassessment_fit_valid`: check
- `research_icpassessment_versions_set`: check

Indexes:

- `rs_icp_client_idx`: (client)
- `rs_icp_latest_idx`: (company, -created_at, -id)
- `rs_icp_profile_idx`: (campaign_profile)
- `rs_icp_research_idx`: (company_research)

### Signal

`research.Signal`, append-only, tenant, table `research_signal`.

| Field | Type | Null | Notes |
| --- | --- | --- | --- |
| id | uuid | no | PK |
| client_id | uuid | no | FK to campaigns.Client |
| company_id | uuid | no | FK to companies.Company |
| data_source_id | uuid | no | FK to companies.DataSource |
| type | text(32) | no | one of: `sales_hiring`, `bd_hiring`, `commercial_hiring`, `partnerships_hiring`, `headcount_growth`, `commercial_team_growth`, `funding`, `market_expansion`, `new_leadership`, `new_office`, `new_product`, `major_partnership`, `other` |
| evidence | text | no |  |
| event_date | date | no |  |
| detected_at | timestamptz | no |  |
| expires_at | timestamptz | yes |  |
| supersedes_id | uuid | yes | FK to research.Signal; unique |
| model_name | text(100) | no |  |
| prompt_version | text(50) | no |  |
| schema_version | text(50) | no |  |
| created_by_id | uuid | yes | FK to accounts.User |
| created_at | timestamptz | no |  |

Constraints:

- `research_signal_evidence_not_empty`: check
- `research_signal_type_valid`: check

Indexes:

- `rs_signal_client_idx`: (client)
- `rs_signal_company_expiry_idx`: (company, expires_at)
- `rs_signal_source_idx`: (data_source)
<!-- END GENERATED: reference -->
