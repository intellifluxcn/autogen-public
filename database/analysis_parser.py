"""Parse analysis markdown files to extract data classification information."""

import re
from typing import Dict, Optional
import logging
from utils.pipeline_log import pipeline_log

logger = logging.getLogger(__name__)


class AnalysisParser:
    """Parse analysis markdown files to extract data classification."""

    @staticmethod
    def parse_doctor_report(content: str) -> Dict[str, Optional[str]]:
        """Extract sequencing and drug data status from Doctor's Report table."""
        result = {'sequencing_status': None, 'drug_status': None}

        doctor_report_pattern = r'##\s+Doctor\'s Report.*?\n(.*?)(?=\n##|\Z)'
        match = re.search(doctor_report_pattern, content, re.DOTALL | re.IGNORECASE)
        if not match:
            return result

        table_section = match.group(1)

        # Extract sequencing status
        seq_pattern = r'\|\s*\*\*Has Sequencing Data\?\*\*\s*\|\s*([^|]+)\s*\|'
        seq_match = re.search(seq_pattern, table_section, re.IGNORECASE)
        if seq_match:
            status = seq_match.group(1).strip()
            if 'Yes (External)' in status or 'Yes(External)' in status:
                result['sequencing_status'] = 'Yes (External)'
            elif status.lower().startswith('yes'):
                result['sequencing_status'] = 'Yes'
            elif status.lower().startswith('no'):
                result['sequencing_status'] = 'No'
            elif 'contact author' in status.lower():
                result['sequencing_status'] = 'Contact Author'
            else:
                result['sequencing_status'] = status

        # Extract drug status
        drug_pattern = r'\|\s*\*\*Has Drug Data\?\*\*\s*\|\s*([^|]+)\s*\|'
        drug_match = re.search(drug_pattern, table_section, re.IGNORECASE)
        if drug_match:
            status = drug_match.group(1).strip()
            if status.lower().startswith('yes'):
                result['drug_status'] = 'Yes'
            elif status.lower().startswith('no'):
                result['drug_status'] = 'No'
            else:
                result['drug_status'] = status

        return result

    @staticmethod
    def parse_suitability_verdict(content: str) -> Optional[str]:
        """Extract 'Has Suitable Data' status."""
        pattern = r'\*\*Has Suitable Data:\*\*\s*(\w+)'
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            status = match.group(1).strip()
            if status.lower().startswith('yes'):
                return 'Yes'
            elif status.lower().startswith('no'):
                return 'No'
            return status
        return None

    @staticmethod
    def parse_browseruse_verdict(content: str) -> Optional[str]:
        """Extract 'Has Downloadable Data' status."""
        pattern = r'\*\*Has Downloadable Data:\*\*\s*(\w+)'
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            status = match.group(1).strip()
            if status.lower().startswith('yes'):
                return 'Yes'
            elif status.lower().startswith('no'):
                return 'No'
            return status
        return None

    @staticmethod
    def classify_data_flag(
        sequencing_status: Optional[str],
        drug_status: Optional[str],
        suitable: Optional[str],
        downloadable: Optional[str]
    ) -> str:
        """Compute data classification flag based on parsed fields."""
        seq_has_data = sequencing_status in ['Yes', 'Yes (External)']
        drug_has_data = drug_status == 'Yes'

        if suitable == 'No' or sequencing_status == 'Contact Author':
            return 'contact_author'
        if suitable == 'Yes' and downloadable == 'No':
            return 'manual_required'
        if seq_has_data and drug_has_data:
            return 'both_data'
        if seq_has_data and not drug_has_data:
            return 'sequencing_only'
        if drug_has_data and not seq_has_data:
            return 'drug_only'
        return 'contact_author'

    @classmethod
    def parse_and_classify(cls, content: str) -> str:
        """Parse content and return classification flag."""
        try:
            doctor_report = cls.parse_doctor_report(content)
            suitable = cls.parse_suitability_verdict(content)
            downloadable = cls.parse_browseruse_verdict(content)

            flag = cls.classify_data_flag(
                doctor_report.get('sequencing_status'),
                doctor_report.get('drug_status'),
                suitable,
                downloadable
            )

            logger.debug(f"Parsed classification: {flag} (seq={doctor_report.get('sequencing_status')}, "
                        f"drug={doctor_report.get('drug_status')}, suitable={suitable}, downloadable={downloadable})")

            return flag
        except Exception as e:
            pipeline_log(
                f"Failed to parse analysis content: {e}",
                stage="analyze",
                component="analysis_parser",
                level=logging.WARNING,
            )
            return 'no_analysis'
