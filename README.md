# AI4MED AutoGen — Cancer Drug Response Dataset Discovery

An automated pipeline that discovers, analyzes, qualifies, and downloads cancer
drug-response datasets from the published literature using LLM agents.

This repository accompanies the paper's data-availability statement: it contains
the **core pipeline code, the prompts, and the usage documentation** needed to
understand and reproduce the method.

## What it does

Researchers building drug-response models need datasets that pair **gene
expression / sequencing data** with **drug response measurements** (IC50, AUC).
Finding them by hand means reading hundreds of papers and navigating dozens of
repositories with inconsistent conventions.

```
Find  →  Analyze  →  Qualify  →  Download
```

1. **Find** — searches Semantic Scholar and PubMed, then downloads open-access
   PDFs through a waterfall of legitimate sources: Europe PMC → ArXiv →
   Unpaywall. An LLM pre-screener drops irrelevant hits early.
2. **Analyze** — an LLM reads each PDF, classifies what data the paper makes
   available, and emits a structured analysis plus a download plan.
3. **Qualify** — validates that a candidate dataset actually contains paired
   expression + drug-response data, rather than merely mentioning it.
4. **Download** — an acquisition router dispatches three strategies:
   repository browser automation, author-contact drafting, and extraction of
   data embedded in the paper's own tables and figures.

## Scope of this repository

This is a **curated publication copy**, not a mirror of the internal
development repository. What is here:

- the four pipeline stages and the code they need to run
- all prompts (`prompts/`, with an index of the ones that are built inline)
- the operating instructions needed to run it (in this README)

What is deliberately **not** here, and why:

| Excluded | Reason |
| --- | --- |
| Web UI (`web_ui/`) and deployment configs | Operational, not part of the method |
| Internal engineering notes (`CLAUDE.md`, `AGENTS.md`) | Internal operations |
| Development documentation (`docs/`) | Accumulated during development, not written for external readers. What you need to *operate* the pipeline is in this README instead |
| Test suite | Depends on internal fixtures |
| Vendor API reference for the browser-automation service | Third-party documentation, not ours to redistribute |
| Any Sci-Hub retrieval path | Removed on purpose — see below |

### Removed: Sci-Hub fallback

The internal version had an optional fourth step in the open-access download
waterfall that queried Sci-Hub mirrors. It was disabled by default and gated
behind an environment variable. **It has been removed from this repository
entirely** — the module, the branch that called it, and the environment switch.

The waterfall here is Europe PMC → ArXiv → Unpaywall, all legitimate
open-access sources. Papers that are not open access are simply not retrieved.
This is a deliberate narrowing of behaviour relative to the internal version and
is documented here so that reproduction attempts are not confused by it.

### Removed: the derived-dataset mirror

Seven of the pharmacogenomics resources (CTRP, gCSI, GRAY, FIMM, UHNBreast,
Tavor, Beat AML) have no machine-readable upstream file — their only live
source is a PharmacoDB PharmacoSet `.rds`, which this pipeline cannot read.
Internally they are obtained by a one-time offline base-R conversion and served
from a private mirror.

**This repository ships no mirror.** Those entries resolve to `None` unless you
set `KNOWN_DB_MIRROR_BASE` to a mirror of your own. Every accession and licence
needed to perform the same conversion is in the table below. The detection and
guidance logic is unchanged; only the direct file URLs are absent.

## Requirements and cost

Reproducing this pipeline is **not free**. Before you start:

- Python 3.11+, Node.js 18+, PostgreSQL 15+
- An **OpenRouter API key** — the Analyze and Download stages make LLM calls,
  and the Analyze prompt alone is on the order of 15 K characters per paper.
  Running the pipeline over a corpus of any size will incur real API charges.
- Optional: a browser-automation service key for repository downloads

We state this plainly because the cost is a genuine barrier to reproduction,
and it is better to know before you begin than after.

## Quick start

```bash
cp .env.example .env      # then fill in DATABASE_URL, AUTH_SECRET_KEY,
                          # LINKED_ACCOUNTS_KEY, OPENROUTER_API_KEY
poetry install
python orchestrator.py --help
```

The backend fails fast at startup if any required variable is missing.

## Writing a research query

The research query is passed to **PubMed verbatim** (max 2000 characters, no
rewriting). Two ways to write it:

- **Plain English keywords** — 2–4 terms describing "tumor type + data
  characteristics", e.g. `breast cancer drug resistance RNA-seq`. MeSH synonym
  expansion is applied automatically (up to 10 synonyms).
- **A boolean PubMed query** — as soon as you use uppercase `AND`/`OR`/`NOT` or
  a field tag such as `[tiab]`, MeSH expansion is skipped and your query is used
  exactly as written.

For this use case the second form is strongly preferred: MeSH adds at most 10
terms, far short of what the three-concept intersection needs. Build it as an
**AND-of-ORs** over three concepts:

```
(in-vitro tumor model) AND (drug screening / sensitivity) AND (transcriptome / RNA-seq)
```

A working query, about 1140 characters:

```
("organoid*"[tiab] OR "PDO"[tiab] OR "PDOs"[tiab] OR "cell line*"[tiab] OR "cell strain*"[tiab] OR "patient-derived"[tiab] OR "primary culture*"[tiab] OR "primary tumor cell*"[tiab] OR "primary tumour cell*"[tiab] OR "cancer stem cell*"[tiab] OR "tumor stem cell*"[tiab] OR "tumour stem cell*"[tiab] OR "stem-like cell*"[tiab] OR "glioma stem*"[tiab] OR "tumor-initiating cell*"[tiab] OR "GSC"[tiab] OR "GSCs"[tiab] OR "tumorsphere*"[tiab] OR "tumoursphere*"[tiab] OR "neurosphere*"[tiab] OR "spheroid*"[tiab]) AND ("drug screen*"[tiab] OR "drug sensitivit*"[tiab] OR "drug response*"[tiab] OR "drug testing"[tiab] OR "chemosensitivit*"[tiab] OR "pharmacogenomic*"[tiab] OR "high-throughput screen*"[tiab] OR "viability assay*"[tiab] OR "dose-response"[tiab] OR "IC50"[tiab] OR "AUC"[tiab] OR "GR metrics"[tiab] OR "drug sensitivity score"[tiab] OR "DSS"[tiab] OR "inhibition rate"[tiab]) AND ("RNA-seq"[tiab] OR "RNAseq"[tiab] OR "RNA sequencing"[tiab] OR "transcriptom*"[tiab] OR "gene expression"[tiab] OR "expression profil*"[tiab] OR "expression matrix"[tiab] OR "microarray"[tiab] OR "mRNA-seq"[tiab] OR "whole-transcriptome"[tiab])
```

Three knobs:

- **Narrow to a tumor type**: append
  `AND ("glioma"[tiab] OR "glioblastoma"[tiab] OR "GBM"[tiab])`
- **Raise recall**: drop `[tiab]`, or drop the sequencing block entirely — many
  papers mention sequencing data only in supplementary material or the data
  availability statement, so that block is the most likely to exclude good papers
- **Widen the net**: set `PRESCREEN_POOL_SIZE` and `PUBMED_MAX_PAGES`; the
  default fetch is only `max_papers × 3`. Set `NCBI_API_KEY` to raise the NCBI
  rate limit

Validate any query in the PubMed web interface first and check "Search details" —
PubMed has edge cases around truncation inside quotes (`"drug screen*"`).

Deliberately kept **out** of the query, because title/abstract cannot support
them: exact drug-name matching, the ≥2-drug requirement, and sample/model
correspondence. Those are decided by the full-text analyze and qualify stages.

## Pharmacogenomics data sources

Nine datasets carry expression **E**, drug response **R**, and a linkable sample
ID **L**, so they can be used for modelling directly. Obtain them from the
authoritative sources below.

| Dataset | Content | Metric | Source | Licence |
|---|---|---|---|---|
| CCLE/DepMap + PRISM | ~1000 lines × PRISM screen | AUC / IC50 | Broad DepMap (figshare 20237739) | Public |
| GDSC1 / GDSC2 | ~1000 lines × targeted + chemo | LN_IC50 / AUC | Sanger cancerRxGene release 8.5 | Public |
| CTRP | 887 lines × 544 drugs | AAC / IC50 | PharmacoDB (Zenodo 3905470) | CC0 |
| gCSI | 410 lines × 16 drugs | AAC / IC50 | PharmacoDB (Zenodo 4742696) | CC0 |
| GRAY | 70 breast lines × 89 drugs | AAC / IC50 / GI50 | PharmacoDB (Zenodo 3905454) | CC0 |
| FIMM | 50 lines × 52 drugs | AAC / IC50 | PharmacoDB (Zenodo 3905448) | CC0 |
| UHNBreast | 56 breast lines × 8 drugs | AAC / IC50 | PharmacoDB (Zenodo 3905460) | CC0 |
| Tavor | 53 patient AML samples × 46 drugs | AAC / IC50 | PharmacoDB (Zenodo 4585705) | CC-BY |
| Beat AML | ~520 patients × ~150 inhibitors | AUC / IC50 | vizome.org · `biodev/beataml2.0_data` | CC-BY 4.0 |

**Shared files for the cell-line datasets (1–7)**: expression
`CCLE_expression.csv` (Broad DepMap, figshare 34989919, ~428 MB) and the link
table `sample_info.csv` (figshare 35020903). Join on the cell-line identifier —
CCLE/PRISM uses `DepMap_ID` (`ACH-xxxxxx`), GDSC uses `COSMIC_ID` plus the line
name, and `sample_info.csv` bridges them.

**Tavor and Beat AML are patient samples**, ship their own expression matrices,
and do not join to CCLE. Beat AML joins on the RNA-seq sample ID `BA####R`
(the `dbgap_rnaseq_sample` column against the expression table's column names),
giving 520 pairable patients; both its files are tab-separated.

Prefer `AAC` / `AUC` over IC50 for modelling — they are more robust. The panels
are complementary and can be merged, but each was measured in a different
laboratory, so watch for batch effects.

> The CTRP, gCSI, GRAY, FIMM, UHNBreast, Tavor, and Beat AML response tables
> have no machine-readable upstream file — the live source is a PharmacoDB
> PharmacoSet `.rds`. Converting one takes a single offline base-R step:
> `readRDS` then `attr(p, "sensitivity")`, written out as a tidy
> `cell_line, drug, aac/ic50` CSV. No PharmacoGx or Bioconductor needed.

## Known limitations

- The **author-contact** strategy drafts outreach emails but the SMTP transport
  lives in the web backend, which is not included here. That strategy will
  report `draft_failed` in this repository; the drafting logic is still readable
  in `download/author_contact_team.py`.
- `config/defaults.json` is read by the web backend and is retained only for
  reference.
- Dataset coverage reflects what the pharmacogenomics repositories exposed at
  the time of the study; repository APIs change.

## License

MIT — see [`LICENSE`](LICENSE). The MIT text covers "this software and
associated documentation files", so the code, the docs and the prompt files in
this repository are all under the same terms.

## Citation

Please cite the accompanying paper. Citation block to be filled in on release.
