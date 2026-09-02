"""Known cancer drug-response resources + detection.

Many papers don't ship their own drug-response (R) or expression (E) data — they
reuse a public pharmacogenomics resource (GDSC, CCLE/DepMap, CTRP, NCI-60, ...).
Auto-downloading the canonical E+R from these is unreliable (JS portals, versioned
figshare links that rot, cell-line linkability), so instead we DETECT the resource
from the paper and surface clear human-retrieval guidance (stable portal landing
pages) in the analysis. This is a deterministic keyword/regex scan — no network.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List

# ---------------------------------------------------------------------------
# Derived-artifact mirror
#
# A few resources below (CTRP, gCSI, GRAY, FIMM, UHNBreast, Tavor, Beat AML)
# have no machine-readable upstream file: their only live source is a
# PharmacoDB PharmacoSet ``.rds``, which this pipeline cannot read. Obtaining
# them requires a ONE-TIME offline base-R extraction
# (``readRDS`` + ``attr(p, "sensitivity")`` -> a tidy cell_line, drug, aac/ic50
# CSV). The result is a derived artifact, not a redistributable upstream file.
#
# This public repository therefore ships NO mirror. Set ``KNOWN_DB_MIRROR_BASE``
# to the base URL of your own mirror of those converted CSVs, and the entries
# below resolve to it. When unset, ``url`` is ``None`` and the ``upstream``
# field points at the authoritative record you should convert from.
#
# See the README ("Pharmacogenomics data sources") for every accession
# and licence.
# ---------------------------------------------------------------------------
_MIRROR_BASE = os.getenv("KNOWN_DB_MIRROR_BASE", "").rstrip("/")


def _mirror(path: str):
    """Resolve a derived-artifact path against KNOWN_DB_MIRROR_BASE, or None."""
    return f"{_MIRROR_BASE}/{path}" if _MIRROR_BASE else None


# Each resource: stable portal landing page (NOT a fragile direct file URL),
# what it provides, and detection patterns (word-boundary, case-insensitive).
KNOWN_RESOURCES: List[Dict] = [
    {
        "name": "GDSC",
        "full_name": "Genomics of Drug Sensitivity in Cancer",
        "portal": "https://www.cancerrxgene.org/downloads/bulk_download",
        "provides": "drug response (IC50/AUC) + cell-line RMA expression",
        "patterns": [r"\bGDSC\d?\b", r"genomics of drug sensitivity in cancer", r"cancerrxgene"],
        # GDSC drug response is live on the Sanger object store; GDSC's own
        # expression direct URL has rotted, so E reuses CCLE_expression (covers
        # most GDSC lines) linked via sample_info's COSMICID↔DepMap_ID crosswalk.
        # GDSC's drug panel differs from PRISM → a distinct strict dataset.
        # Probed live 2026-06-21.
        "datasets": [
            {"role": "R", "filename": "GDSC2_fitted_dose_response.csv",
             "url": "https://cog.sanger.ac.uk/cancerrxgene/GDSC_release8.5/GDSC2_fitted_dose_response_27Oct23.csv"},
            {"role": "R", "filename": "GDSC1_fitted_dose_response.csv",
             "url": "https://cog.sanger.ac.uk/cancerrxgene/GDSC_release8.5/GDSC1_fitted_dose_response_27Oct23.csv"},
            {"role": "E", "filename": "CCLE_expression.csv",
             "url": "https://ndownloader.figshare.com/files/34989919"},
            {"role": "linkage", "filename": "sample_info.csv",
             "url": "https://ndownloader.figshare.com/files/35020903"},
        ],
    },
    {
        "name": "CCLE / DepMap",
        "full_name": "Cancer Cell Line Encyclopedia / Dependency Map",
        "portal": "https://depmap.org/portal/download/",
        "provides": "cell-line expression (CCLE) + drug sensitivity (PRISM/GDSC reprocessed)",
        "patterns": [r"\bCCLE\b", r"\bDepMap\b", r"cancer cell line encyclopedia", r"dependency map", r"\bPRISM\b"],
        # Confirmed-live stable file endpoints (figshare ndownloader). E and R
        # both keyed by DepMap_ID, so they form a linkable E+R unit. Probed
        # 2026-06-21.
        "datasets": [
            {"role": "R", "filename": "PRISM_secondary_dose_response_curve_parameters.csv",
             "url": "https://ndownloader.figshare.com/files/20237739"},
            {"role": "E", "filename": "CCLE_expression.csv",
             "url": "https://ndownloader.figshare.com/files/34989919"},
            {"role": "linkage", "filename": "sample_info.csv",
             "url": "https://ndownloader.figshare.com/files/35020903"},
        ],
    },
    {
        "name": "CTRP",
        "full_name": "Cancer Therapeutics Response Portal",
        "portal": "https://ctd2-data.nci.nih.gov/Public/Broad/",
        "provides": "drug response (AUC) for CCLE cell lines",
        "patterns": [r"\bCTRP\b", r"\bCTRPv\d\b", r"cancer therapeutics response portal"],
        # NCI CTD2 CSV/FTP source is retired (probed dead 2026-07-09: host 301→
        # studycatalog, FTP dirs empty). The only live source is the PharmacoDB
        # CTRPv2 PharmacoSet .rds (Zenodo 3905470, CC0), which the pipeline can't
        # read. Resolution: a one-time offline PharmacoGx-free base-R extraction
        # (readRDS + attr(p,"sensitivity") → tidy cell_line,drug,aac/ic50 CSV);
        # see the mirror note at the top of this file. E reuses CCLE_expression,
        # L reuses sample_info (CTRP cell-line names link to CCLE: 825/887 =
        # 93%). Verified strict.
        "datasets": [
            {"role": "R", "filename": "CTRPv2_sensitivity.csv",
             "url": _mirror("known_db/ctrp/CTRPv2_sensitivity.csv")},
            {"role": "E", "filename": "CCLE_expression.csv",
             "url": "https://ndownloader.figshare.com/files/34989919"},
            {"role": "linkage", "filename": "sample_info.csv",
             "url": "https://ndownloader.figshare.com/files/35020903"},
        ],
    },
    {
        "name": "NCI-60",
        "full_name": "NCI-60 / CellMiner",
        "portal": "https://discover.nci.nih.gov/cellminer/",
        "provides": "NCI-60 expression + drug response (GI50) via CellMiner",
        "patterns": [r"\bNCI-?60\b", r"\bCellMiner\b", r"\bNCI/?DTP\b", r"developmental therapeutics program"],
        # No auto-fetch: NCI-60 R (CellMiner DTP) is reachable, but its tissue-
        # prefixed names (BR:MCF7) won't link to CCLE's ACH-ID expression (the
        # name->ACH crosswalk through sample_info is too fuzzy for qualify), and
        # NCI-60's own expression is legacy .xls (no reader; CI won't add deps).
        # Low marginal value (60 lines, mostly a CCLE subset). Detection-only.
    },
    {
        "name": "PRISM",
        "full_name": "PRISM Repurposing (Broad)",
        "portal": "https://depmap.org/repurposing/",
        "provides": "large-scale drug sensitivity (viability) across cell lines",
        "patterns": [r"\bPRISM\b", r"repurposing secondary screen"],
    },
    {
        "name": "gCSI",
        "full_name": "Genentech Cell Line Screening Initiative",
        "portal": "https://pharmacodb.ca/",
        "provides": "drug response across cell lines (via PharmacoDB)",
        "patterns": [r"\bgCSI\b", r"genentech cell line screening"],
        # Genentech industrial-grade RNA-seq + drug response; only distributed as
        # a PharmacoSet .rds (Zenodo 4742696, CC0). Same one-time base-R extraction
        # as CTRP → tidy R CSV. E reuses CCLE_expression, L reuses
        # sample_info (cell-line names align to CCLE/COSMIC). Independent drug
        # panel → a distinct strict dataset.
        "datasets": [
            {"role": "R", "filename": "gCSI_sensitivity.csv",
             "url": _mirror("known_db/gcsi/gCSI_sensitivity.csv")},
            {"role": "E", "filename": "CCLE_expression.csv",
             "url": "https://ndownloader.figshare.com/files/34989919"},
            {"role": "linkage", "filename": "sample_info.csv",
             "url": "https://ndownloader.figshare.com/files/35020903"},
        ],
    },
    {
        "name": "GRAY",
        "full_name": "Gray Lab Breast Cancer (LBNL/OHSU)",
        "portal": "https://pharmacodb.ca/",
        "provides": "breast-cancer cell-line drug response (GI50/AUC) + expression",
        "patterns": [r"gray\s*lab", r"\bheiser\b", r"\bdaemen\b", r"GRAY_?2017"],
        # ~70 breast-cancer cell lines (all subtypes) × ~89 compounds. Only
        # distributed as a PharmacoSet .rds (Zenodo 3905454, CC0). Same one-time
        # base-R extraction as CTRP → tidy R CSV. E reuses CCLE_expression,
        # L reuses sample_info. Breast-subtype external-validation set.
        "datasets": [
            {"role": "R", "filename": "GRAY_sensitivity.csv",
             "url": _mirror("known_db/gray/GRAY_sensitivity.csv")},
            {"role": "E", "filename": "CCLE_expression.csv",
             "url": "https://ndownloader.figshare.com/files/34989919"},
            {"role": "linkage", "filename": "sample_info.csv",
             "url": "https://ndownloader.figshare.com/files/35020903"},
        ],
    },
    {
        "name": "FIMM",
        "full_name": "Institute for Molecular Medicine Finland cell-line screen",
        "portal": "https://pharmacodb.ca/",
        "provides": "cell-line drug response (AAC/IC50)",
        "patterns": [r"\bFIMM\b", r"institute for molecular medicine finland"],
        # 50 cell lines × 52 drugs. Only distributed as a PharmacoSet .rds
        # (Zenodo 3905448, CC0). Same one-time base-R extraction as CTRP → tidy
        # R CSV. E reuses CCLE_expression, L reuses sample_info.
        "datasets": [
            {"role": "R", "filename": "FIMM_sensitivity.csv",
             "url": _mirror("known_db/fimm/FIMM_sensitivity.csv")},
            {"role": "E", "filename": "CCLE_expression.csv",
             "url": "https://ndownloader.figshare.com/files/34989919"},
            {"role": "linkage", "filename": "sample_info.csv",
             "url": "https://ndownloader.figshare.com/files/35020903"},
        ],
    },
    {
        "name": "UHNBreast",
        "full_name": "University Health Network breast cancer screen",
        "portal": "https://pharmacodb.ca/",
        "provides": "breast-cancer cell-line drug response (AAC/IC50)",
        "patterns": [r"\bUHN[-\s]?Breast\b"],
        # 56 breast-cancer cell lines × 8 drugs. Only distributed as a
        # PharmacoSet .rds (Zenodo 3905460, CC0). Same base-R extraction as CTRP.
        # E reuses CCLE_expression, L reuses sample_info. Breast external set.
        "datasets": [
            {"role": "R", "filename": "UHNBreast_sensitivity.csv",
             "url": _mirror("known_db/uhnbreast/UHNBreast_sensitivity.csv")},
            {"role": "E", "filename": "CCLE_expression.csv",
             "url": "https://ndownloader.figshare.com/files/34989919"},
            {"role": "linkage", "filename": "sample_info.csv",
             "url": "https://ndownloader.figshare.com/files/35020903"},
        ],
    },
    {
        "name": "Tavor",
        "full_name": "Tavor 2020 ex-vivo AML patient drug screen",
        "portal": "https://pharmacodb.ca/",
        "provides": "patient AML ex-vivo drug response (AAC/IC50) + own RNA-seq",
        "patterns": [r"\bTavor2020\b", r"\bTavor\b.{0,30}(AML|leukemi|ex.?vivo)"],
        # SELF-CONTAINED unit (NOT reuse-CCLE): Tavor is 53 primary AML patient
        # samples keyed by numeric patient IDs, which do NOT map to CCLE cell
        # lines. Its PharmacoSet .rds (Zenodo 4585705, CC-BY) ships its OWN
        # RNA-seq (molecularProfiles$rnaseq, 43 samples × 24k genes), extracted
        # alongside R by base R. E and R link directly on the
        # shared patient IDs — no CCLE, no sample_info. Fills the patient-AML gap.
        "datasets": [
            {"role": "R", "filename": "Tavor_sensitivity.csv",
             "url": _mirror("known_db/tavor/Tavor_sensitivity.csv")},
            {"role": "E", "filename": "Tavor_expression.csv",
             "url": _mirror("known_db/tavor/Tavor_expression.csv")},
        ],
    },
    {
        "name": "BeatAML",
        "full_name": "Beat AML (Tyner 2018 / Bottomly 2022) patient AML ex-vivo screen",
        "portal": "http://www.vizome.org/",
        "provides": "patient AML ex-vivo drug response (probit AUC/IC50) + own RNA-seq",
        "patterns": [
            r"\bBeat[-\s]?AML\b",
            r"\bTyner\b.{0,40}(AML|leukemi|functional genomic)",
            r"\bvizome\b",
        ],
        # SELF-CONTAINED unit (like Tavor, not reuse-CCLE): ~520 primary AML
        # patient samples keyed by BA####R IDs that do NOT map to CCLE cell lines.
        # Beat AML 2.0 ships plain TSV (no .rds wall) on the public GitHub repo
        # biodev/beataml2.0_data (CC-BY 4.0). R = probit curve fits (ic50/auc,
        # key col dbgap_rnaseq_sample); E = normalized RNA-seq (genes × 707
        # samples, sample IDs as header columns). E and R link directly on the
        # shared BA####R IDs — no CCLE, no sample_info. E is ~281MB → qualify
        # head-truncates it, but the sample columns live in the (intact) header
        # row so R×E-header linkage stays complete.
        "datasets": [
            {"role": "R", "filename": "BeatAML_sensitivity.txt",
             "url": _mirror("known_db/beataml/BeatAML_sensitivity.txt")},
            {"role": "E", "filename": "BeatAML_expression.txt",
             "url": _mirror("known_db/beataml/BeatAML_expression.txt")},
        ],
    },
    {
        "name": "GDSC/CCLE via PharmacoGx",
        "full_name": "PharmacoDB / ORCESTRA PharmacoSets",
        "portal": "https://pharmacodb.ca/",
        "provides": "harmonised E+R PharmacoSets for many resources",
        "patterns": [r"\bPharmacoGx\b", r"\bPharmacoDB\b", r"\bORCESTRA\b", r"PharmacoSet"],
    },
]

_COMPILED = [
    (r, [re.compile(p, re.IGNORECASE) for p in r["patterns"]]) for r in KNOWN_RESOURCES
]


def detect_known_resources(text: str) -> List[Dict]:
    """Return the known resources referenced in ``text`` (deduped, first-match
    order). Empty when none. No network."""
    if not text:
        return []
    found: List[Dict] = []
    seen = set()
    for resource, regexes in _COMPILED:
        if resource["name"] in seen:
            continue
        if any(rx.search(text) for rx in regexes):
            found.append(resource)
            seen.add(resource["name"])
    return found


def resources_with_datasets(text: str) -> List[Dict]:
    """Detected resources that have auto-fetchable stable file endpoints (a
    ``datasets`` list) — the subset the download stage can directly retrieve."""
    return [r for r in detect_known_resources(text) if r.get("datasets")]


def render_resource_guidance(resources: List[Dict]) -> str:
    """Render a markdown guidance section for detected resources, or '' if none."""
    if not resources:
        return ""
    lines = [
        "",
        "## Data Source Guidance",
        "",
        "This paper references the public pharmacogenomics resource(s) below. If the "
        "automated download did not capture a usable expression (E) and/or "
        "drug-response (R) matrix, retrieve it manually from the resource's portal "
        "(these are not reliably auto-downloadable — JS portals / versioned links):",
        "",
    ]
    for r in resources:
        lines.append(
            f"- **{r['name']}** ({r['full_name']}) — provides {r['provides']}. "
            f"Download: {r['portal']}"
        )
    lines.append("")
    return "\n".join(lines)
