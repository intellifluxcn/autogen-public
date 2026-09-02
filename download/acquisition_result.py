"""Shared result models for acquisition task execution."""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


TaskStatus = Literal["completed", "partial", "skipped", "failed"]
ArtifactType = Literal["dataset", "embedded_dataset", "acquisition_evidence"]
AcquisitionSource = Literal[
    "repository_download",
    "supplementary_download",
    "author_attachment",
    "paper_table_extraction",
    "paper_figure_extraction",
    "contact_author",
    "repository_evidence",
    "controlled_access",
]
TrustLevel = Literal["high", "medium", "low"]


class CoverageSummary(BaseModel):
    """Coverage status for desired data types."""

    sequencing_data: bool = Field(False, description="Whether sequencing data was acquired")
    drug_response_data: bool = Field(False, description="Whether drug response data was acquired")


class ProducedArtifact(BaseModel):
    """Structured descriptor for one acquisition artifact."""

    file_path: str = Field(description="Local or stored artifact path")
    artifact_type: ArtifactType = Field(description="Artifact category")
    acquisition_source: AcquisitionSource = Field(description="How the artifact was acquired")
    acquisition_status: str = Field(description="Artifact-level acquisition status")
    trust_level: TrustLevel = Field(description="Artifact trust classification")
    confidence: Optional[float] = Field(None, description="Confidence from 0.0 to 1.0 when applicable")
    produced_by: Optional[str] = Field(None, description="Team/class that produced the artifact")
    provenance: Dict[str, Any] = Field(default_factory=dict)


class AcquisitionTaskResult(BaseModel):
    """Normalized result for one acquisition task."""

    task_type: str = Field(description="Task strategy that produced this result")
    status: TaskStatus = Field(description="Execution outcome")
    produced_artifacts: List[ProducedArtifact] = Field(default_factory=list)
    coverage: CoverageSummary = Field(default_factory=CoverageSummary)
    confidence: Optional[float] = Field(
        None, description="Confidence from 0.0 to 1.0 when applicable"
    )
    remaining_gaps: List[str] = Field(default_factory=list)
    message: Optional[str] = Field(None, description="Short human-readable summary")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AcquisitionRunResult(BaseModel):
    """Aggregated result across one or more acquisition tasks."""

    status: TaskStatus = Field(description="Overall execution status")
    primary_strategy: str = Field(description="Primary strategy from the acquisition plan")
    task_results: List[AcquisitionTaskResult] = Field(default_factory=list)
    produced_artifacts: List[ProducedArtifact] = Field(default_factory=list)
    coverage: CoverageSummary = Field(default_factory=CoverageSummary)
    confidence: Optional[float] = Field(None)
    remaining_gaps: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_task_results(
        cls,
        *,
        primary_strategy: str,
        task_results: List[AcquisitionTaskResult],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "AcquisitionRunResult":
        produced_artifacts: List[ProducedArtifact] = []
        remaining_gaps: List[str] = []
        confidence_values: List[float] = []
        outcome_counts: Dict[str, int] = {}
        source_counts: Dict[str, int] = {}

        sequencing_data = False
        drug_response_data = False
        any_completed = False
        any_partial = False

        for task_result in task_results:
            produced_artifacts.extend(task_result.produced_artifacts)
            remaining_gaps.extend(task_result.remaining_gaps)
            sequencing_data = sequencing_data or task_result.coverage.sequencing_data
            drug_response_data = drug_response_data or task_result.coverage.drug_response_data
            if task_result.confidence is not None:
                confidence_values.append(task_result.confidence)
            if task_result.status == "completed":
                any_completed = True
            elif task_result.status == "partial":
                any_partial = True
            route_category = str(task_result.metadata.get("route_category") or "")
            if route_category:
                outcome_counts[route_category] = outcome_counts.get(route_category, 0) + 1
            for artifact in task_result.produced_artifacts:
                source_counts[artifact.acquisition_source] = (
                    source_counts.get(artifact.acquisition_source, 0) + 1
                )
                if artifact.artifact_type == "dataset":
                    key = "repository_dataset_acquired"
                elif artifact.artifact_type == "embedded_dataset":
                    key = "embedded_dataset_extracted"
                else:
                    key = artifact.acquisition_source
                outcome_counts[key] = outcome_counts.get(key, 0) + 1

        if task_results and all(result.status == "skipped" for result in task_results):
            status: TaskStatus = "skipped"
        elif any(result.status == "failed" for result in task_results) and not any_completed and not any_partial:
            status = "failed"
        elif any_partial or any(result.status == "failed" for result in task_results):
            status = "partial"
        else:
            status = "completed"

        deduped_artifacts: List[ProducedArtifact] = []
        seen_paths = set()
        for artifact in produced_artifacts:
            if artifact.file_path in seen_paths:
                continue
            seen_paths.add(artifact.file_path)
            deduped_artifacts.append(artifact)

        result_metadata = dict(metadata or {})
        result_metadata["outcome_counts"] = outcome_counts
        result_metadata["source_counts"] = source_counts
        result_metadata["artifact_quality"] = {
            "produced_file_count": len(deduped_artifacts),
            "duplicate_file_count": max(0, len(produced_artifacts) - len(deduped_artifacts)),
        }
        result_metadata["coverage_flags"] = {
            "sequencing_data": sequencing_data,
            "drug_response_data": drug_response_data,
            "both_modalities": sequencing_data and drug_response_data,
        }

        return cls(
            status=status,
            primary_strategy=primary_strategy,
            task_results=task_results,
            produced_artifacts=deduped_artifacts,
            coverage=CoverageSummary(
                sequencing_data=sequencing_data,
                drug_response_data=drug_response_data,
            ),
            confidence=(sum(confidence_values) / len(confidence_values)) if confidence_values else None,
            remaining_gaps=list(dict.fromkeys(remaining_gaps)),
            metadata=result_metadata,
        )
