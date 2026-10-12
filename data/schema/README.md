# Dataset schema v1

A dataset directory contains:

| File | Public? | Purpose |
|------|---------|---------|
| `dataset_meta.txt` | yes (no PII) | `key=value`: `schema_version=1`, `dataset_id`, `dataset_version`, `kind=real|synthetic`, `protocol=speaker_independent|personalised` |
| `wordlist.csv` | yes | words, roles, minimal pairs, review status |
| `recordings.csv` | private | one row per recording, no personal attributes |
| `participants.csv` | **private** | optional voluntary attributes + consent record, keyed by `speaker_id` |
| `audio/...wav` | **private** | original recordings, unmodified |

CSV rules: UTF-8 (no BOM), comma separated, `"` quoting, LF or CRLF. Line 1 is a version tag
(`#s2w-wordlist v1`, `#s2w-recordings v1`, `#s2w-participants v1`), line 2 the header, then rows.
Column order is free but every listed column must exist; unknown columns are errors.
Identifiers (`word_id`, `speaker_id`, `session_id`, `recording_id`, `device_id`) match
`[A-Za-z0-9._-]{1,40}`. Paths are relative to the dataset directory, use `/`, end in `.wav`,
and never contain `..`.

Enumerations
- `role`: `vocab` | `oov`
- `contrast_type`: `none` | `vowel_length` | `gemination` | `other`
- `review_status`: `draft` | `reviewed` | `verified` (verified needs two distinct reviewers)
- `split`: `enrol` | `calib` | `test`
- `environment`: `clean` | `noisy`
- `label_verified`, `group_reporting_ok`: `yes` | `no`
- participant `sex_self_report`, `age_band`, `accent_region`: free short text, or `declined`/empty

`participants.csv` has an optional `consent_status` column (`consented`|`withdrawn`), **required for real datasets**.

Templates: `wordlist.template.csv`, `recordings.template.csv`, `participants.template.csv`.

Private registers (templates only here; the real files stay **outside** the repository): `participant_register_template.csv` (identity *reference*, screening, status),
`consent_register_template.csv` (consent choices, retention, withdrawal), `pilot_approvals_template.csv` (gate approvals, all `pending`), `session_log.template.csv`.
Pilot spec: `pilot_conditions.csv`, `pilot_matrix.csv`.
Validate with `build/s2w_dataset_check <dir>`.
