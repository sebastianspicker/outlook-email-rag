# Bilingual And Translation-Aware Workflows

Version: `1`

Status: `approved_for_implementation`

This workspace now supports bilingual legal-support rendering for matters where source evidence is primarily German but counsel-facing work products may need English.

## Scope

The bilingual layer currently applies to:

- `lawyer_issue_matrix`
- `lawyer_briefing_memo`
- `controlled_factual_drafting`
- `case_dashboard`

It also adds shared run metadata at:

- top-level `case_analysis.bilingual_workflow`
- top-level `investigation_report.bilingual_workflow`

## Core rules

- original-language evidence must remain preserved
- translated or output-language summaries must stay separate from quoted evidence
- the system may render narrative summaries in the requested output language without rewriting the source record
- quoted evidence must remain tied to the original-language text fields

## Input controls

`EmailCaseAnalysisInput` and `EmailLegalSupportInput` now accept:

- `output_language`
  - `en`
  - `de`
- `translation_mode`
  - `source_only`
  - `translation_aware`

## Shared metadata

`bilingual_workflow` contains:

- `version`
- `output_language`
- `output_language_label`
- `translation_mode`
- `primary_source_language`
- `primary_source_language_label`
- `source_languages`
- `source_language_labels`
- `source_language_counts`
- `preserve_original_quotations`
- `translated_summaries_allowed`
- `cross_language_rendering`
- `translation_boundary`

## Evidence preservation

`matter_evidence_index.rows[*]` now also carry:

- `source_language`
- `quoted_evidence`
  - `original_language`
  - `original_language_label`
  - `original_text`
  - `quote_translation_included`
  - `translated_summary_fields`

This keeps the original German-source passage visible even when the surrounding product is rendered for English-facing counsel workflows.
