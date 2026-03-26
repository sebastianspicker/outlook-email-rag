"""Pydantic input models for MCP tools.

All MCP tool parameter models are defined in sub-modules and re-exported here
so that ``from src.mcp_models import SomeModel`` continues to work everywhere.

Sub-modules:
- ``mcp_models_base``: Base classes (StrictInput, PlainInput, DateRangeInput).
- ``mcp_models_search``: Search, browse, export, scan, and ingestion inputs.
- ``mcp_models_evidence``: Evidence management, chain of custody, dossier inputs.
- ``mcp_models_analysis``: Network, entity, thread, temporal, quality, and report inputs.
"""

from .mcp_models_analysis import (
    ActionItemsInput,
    CoordinatedTimingInput,
    DecisionsInput,
    EmailAdminInput,
    EmailAttachmentsInput,
    EmailClustersInput,
    EmailContactsInput,
    EmailQualityInput,
    EmailReportInput,
    EmailTemporalInput,
    EmailThreadLookupInput,
    EmailTopicsInput,
    EntityNetworkInput,
    EntitySearchInput,
    EntityTimelineInput,
    ListEntitiesInput,
    NetworkAnalysisInput,
    RelationshipPathsInput,
    RelationshipSummaryInput,
    SharedRecipientsInput,
    ThreadSummaryInput,
)
from .mcp_models_base import (
    DateRangeInput,
    PlainInput,
    StrictInput,
    _validate_output_path,
)
from .mcp_models_evidence import (
    CustodyChainInput,
    EmailDossierInput,
    EmailProvenanceInput,
    EvidenceAddBatchInput,
    EvidenceAddInput,
    EvidenceExportInput,
    EvidenceGetInput,
    EvidenceOverviewInput,
    EvidenceProvenanceInput,
    EvidenceQueryInput,
    EvidenceRemoveInput,
    EvidenceUpdateInput,
)
from .mcp_models_search import (
    BrowseInput,
    EmailDeepContextInput,
    EmailDiscoveryInput,
    EmailExportInput,
    EmailIngestInput,
    EmailScanInput,
    EmailSearchStructuredInput,
    EmailTriageInput,
    FindSimilarInput,
    ListSendersInput,
)

__all__ = [
    "ActionItemsInput",
    "BrowseInput",
    "CoordinatedTimingInput",
    "CustodyChainInput",
    "DateRangeInput",
    "DecisionsInput",
    "EmailAdminInput",
    "EmailAttachmentsInput",
    "EmailClustersInput",
    "EmailContactsInput",
    "EmailDeepContextInput",
    "EmailDiscoveryInput",
    "EmailDossierInput",
    "EmailExportInput",
    "EmailIngestInput",
    "EmailProvenanceInput",
    "EmailQualityInput",
    "EmailReportInput",
    "EmailScanInput",
    "EmailSearchStructuredInput",
    "EmailTemporalInput",
    "EmailThreadLookupInput",
    "EmailTopicsInput",
    "EmailTriageInput",
    "EntityNetworkInput",
    "EntitySearchInput",
    "EntityTimelineInput",
    "EvidenceAddBatchInput",
    "EvidenceAddInput",
    "EvidenceExportInput",
    "EvidenceGetInput",
    "EvidenceOverviewInput",
    "EvidenceProvenanceInput",
    "EvidenceQueryInput",
    "EvidenceRemoveInput",
    "EvidenceUpdateInput",
    "FindSimilarInput",
    "ListEntitiesInput",
    "ListSendersInput",
    "NetworkAnalysisInput",
    "PlainInput",
    "RelationshipPathsInput",
    "RelationshipSummaryInput",
    "SharedRecipientsInput",
    "StrictInput",
    "ThreadSummaryInput",
    "_validate_output_path",
]
