# ai4med 1.0 Data Shape & Alignment Spec

> **Purpose**: This document fixes, as a single checked-in artifact, the data
> shape and alignment/normalization rules that ai4med **1.0** uses for cancer
> drug-response training data. It is the reference the qualify-stage validation
> prompt (`qualify/validation_prompt.md`) injects to decide whether a freshly
> downloaded dataset is `strict` / `loose` / `fail`.
>
> Every rule below is **extracted from the read-only 1.0 reference repo**
> (`/Users/liupengfei/python/ai4med`) with the exact source file + line cited.
> Where a rule is **newly defined for the qualify stage** (because 1.0 does not
> implement it), that is called out explicitly — it is NOT "copied from 1.0".

---

## 1. Two data sources, one shape

1.0 trains on a drug-response problem assembled from **two independent data
families** that happen to share the same table shape:

| Source family | Expression provenance | Drug-response provenance | Code |
|---|---|---|---|
| **chenlab / PDGC** (glioma patient-derived cultures) | RNA-seq **raw counts**, rows = genes, cols = cell lines (`PDGC_count_matrix.txt`) | in-house drug screen, `*.log2fold.A/B` per replicate | `chenlab/data.py`, `ag/chenlab.py` |
| **CCLE + CTRP** (public reference panel) | ssGSEA signature `.gct` files | CTRP `area_under_curve` (`v22.data.auc_sensitivities.txt`) | `ag/data_source.py:GlioMLOrigSource`, `ag/ccle_data.py` |

The unifying contract (`ag/data_source.py:15-21`) is:

> "every row is a cell-line name and every column is expression level of a gene"
> "every row is a cell-line name and every column is a drug test result"

So after assembly, **both matrices are indexed by cell-line / sample name**, and
the join that builds a training set is purely a row-index (cell-line name)
intersection — see §4.

### Required data shape (for any candidate dataset)

A candidate dataset must contain, in tabular form:

1. **Gene expression matrix** — samples × genes (or transposable to it).
2. **Drug response matrix** — samples × drugs, values numeric (AUC or an
   equivalent sensitivity scalar).
3. A **shared sample/cell-line identifier** that links (1) and (2) after
   name normalization (§4).

Missing any one of these three ⇒ `fail`.

---

## 2. Gene ID system

- **chenlab raw expression uses Ensembl gene IDs (`ENSG...`).**
  Confirmed by the header of `ag/chenlab_data/PDGC_count_matrix.txt`: the first
  data column of row 2 is `ENSG00000000003`, and `ag/chenlab.py:64-66` reads
  `gene_id = tokens[0]` verbatim as the index with no ID translation.
- A **gene-SYMBOL variant** of the same matrix exists
  (`PDGC_count_matrix_SYMBOL.csv`, read at `ag/ssgsea_analysis.py:18`) and is the
  input to ssGSEA. So 1.0 in practice handles **both ENSG and HGNC symbol**
  representations of the same genes, but the conversion happens **offline /
  upstream** (a pre-made `_SYMBOL.csv` file) — there is **no ENSG↔symbol mapping
  code in the repo**.
- The CCLE/CTRP path feeds ssGSEA `.gct` signature files
  (`ag/data_source.py:160-168`), i.e. **gene-set scores**, not raw gene IDs.

**Takeaway**: 1.0's feature space is, depending on the path, either ENSG gene IDs,
HGNC symbols, or ssGSEA gene-set scores — and **1.0 performs no automatic gene-ID
mapping at runtime** (it assumes inputs are already in the expected ID space;
joins are by column-name / position only). This is the central fact behind the
**newly defined** gene-ID-mapping policy in §6.

---

## 3. Expression & drug-response encoding

### Expression
- **Raw integer counts** in `PDGC_count_matrix.txt` (e.g. `ENSG00000000003 795 425 617 ...`), read as-is at `ag/chenlab.py:65-66`.
- A **`log2(x+1)`** transform is applied before ssGSEA: `exp4 = np.log2(exp4 + 1)` at `ag/ssgsea_analysis.py:31`.
- The CCLE path additionally **min-max normalizes** each feature column to `[0,1]` (`ag/data_source.py:217-226`, `GlioMLOrigSource.normalize`).

So accepted expression encodings: **raw counts**, **`log2(x+1)`-transformed
counts**, **ssGSEA scores**, or min-max normalized variants thereof. The qualify
prompt should accept any of these as valid "expression" — what matters is that
it is a samples × genes numeric matrix, not the specific normalization.

### Drug response
- **chenlab**: per-replicate `*.log2fold.A` / `*.log2fold.B` columns, averaged across replicates (`ag/chenlab.py:33-35` mean of replicate columns); compound is the row label, cell line the column (`chenlab/data.py:5-13`).
- **CCLE/CTRP**: `area_under_curve` (AUC) values pivoted to a cell-line × compound matrix (`ag/ccle_data.py:11-16`).
- **Missing drug response is encoded as `-inf`** (negative infinity), consistently:
  - `ag/ccle_data.py:13` — `.fillna("-inf")` after the AUC pivot.
  - `ag/data_source.py:254` — `float("-inf" if sub[4 + i] == "NaN" else ...)`.
  - `ag/data_source.py:72-73` — missing drug filled with `np.NINF`.
  - Training rows with a missing label are dropped via `train_dataset[train_dataset[label] > float("-inf")]` (`ag/data_source.py:40`).

So: **drug response is a numeric sensitivity scalar (canonically AUC); missing
values are `-inf`/NaN** and dropped per-label at training time. A candidate
dataset's response column need not literally be named "AUC", but it must be a
numeric per-(sample, drug) sensitivity measure.

---

## 4. Cell-line / sample name normalization (the join key)

1.0 links expression to drug response **purely by normalized cell-line name
intersection** — there is no ID-mapping table for samples either. The exact
normalization steps (from `chenlab/data.py:27-37` and `ag/ccle_data.py:32`):

1. **`CB-` → `CB`**: strip the hyphen after a `CB` prefix
   (`col.replace("CB-", "CB")`, `chenlab/data.py:28`). e.g. `CB-1507` → `CB1507`.
2. **`-1` suffix handling** (`chenlab/data.py:30-33`): if a name ends with `-1`,
   then — if the third-from-last char is a digit, drop the `-1` entirely;
   otherwise drop the `-` and keep the `1` (i.e. `-1` → `1`).
   e.g. `G118-1` → `G118` (3rd-last `8` is a digit); `Gx-1` → `Gx1`.
3. **Uppercase everything** (`chenlab/data.py:35`, `ccle_data.py` via `.upper()`
   on AUC columns at `chenlab/data.py:16-17`). e.g. `GBM06` stays, `gsc11` → `GSC11`.
4. **`_`-prefix split** (CCLE side, `ag/ccle_data.py:32`):
   `ccl_name.split("_")[0]` — keep only the token before the first underscore
   (CCLE names look like `A549_LUNG` → `A549`).
5. **Join = set intersection** of the two normalized index sets
   (`chenlab/data.py:37` `columns.intersection(...)`;
   `ag/ccle_data.py:27,33` `common_idx.intersection(...)`). Only samples present
   in BOTH expression and drug tables survive.

**For the qualify stage**: a candidate dataset is "linkable by cell-line ID" if,
after applying steps 1–4, the normalized sample IDs of its expression matrix and
its drug-response matrix have a **non-empty intersection**. Empty intersection
⇒ not linkable ⇒ `fail`.

---

## 5. Drug names = 1.0 compound list (lowercase exact match)

The 1.0 compound vocabulary has two layers:

- **Curated training drugs** — `ag/configs.py:5-28` `common_drugs`, 21 names, all
  lowercase: `temozolomide, procarbazine, cyclophosphamide, dacarbazine,
  ifosfamide, doxorubicin, etoposide, vincristine, olaparib, dasatinib,
  gefitinib, erlotinib, sunitinib, tacedinaline, vorinostat, belinostat,
  sirolimus, nilotinib, afatinib, topotecan, bortezomib`.
  (`lomustine` is present but commented out.)
- **Full CTRP compound table** — `v22.meta.per_compound.txt`, column `cpd_name`
  (481 compounds), mapped onto the AUC matrix columns at `ag/ccle_data.py:9,16`.

**Matching rule**: drug names are matched **lowercase, exact string** against the
compound list. 1.0 derives drug columns directly from `cpd_name`
(`ag/ccle_data.py:16`) and `common_drugs` entries are authored lowercase; the
chenlab `compound` column is likewise the row label (`chenlab/data.py:5`,
`ag/chenlab.py:8`). No fuzzy/synonym matching exists in 1.0.

**For the qualify stage `strict` test**: a candidate's drug set must be a
**subset of the 1.0 compound list** (`common_drugs` ∪ `per_compound.cpd_name`)
under lowercase exact match. The full `per_compound` list is large (481 names);
the prompt should treat **`common_drugs` as the primary, always-available list**
and `per_compound` as an extended allowlist the qualify runner may inject.

---

## 6. Gene-ID-mapping policy for `strict` — **NEWLY DEFINED**

> **This rule does NOT exist in 1.0.** 1.0 does no runtime gene-ID mapping; it
> assumes inputs are pre-aligned and joins by column name/position
> (`ag/data_source.py`, `ag/ccle_data.py`). Because the qualify stage must judge
> *arbitrary* downloaded datasets — whose gene IDs will NOT already match 1.0's
> feature space — we must define a mapping + overlap policy ourselves. The
> sanity check (§7) is the falsifiable floor that keeps this honest.

**Accepted gene-ID systems** (any one; the dataset declares or is auto-detected):
- **Ensembl gene IDs** — `ENSG\d{11}` (1.0 chenlab native, §2).
- **HGNC gene symbols** — e.g. `TP53`, `EGFR` (1.0 `_SYMBOL.csv` variant, §2).
- **Entrez/NCBI gene IDs** — bare integers interpreted as gene IDs.

Datasets using gene-set scores (ssGSEA pathway names) are treated as a separate,
non-gene-ID feature space: they are accepted for `loose` but **cannot satisfy the
`strict` gene-overlap test** (there is no canonical gene-ID set to overlap).

**Mapping**: identify the candidate's ID system, then compute overlap against
1.0's reference gene set in the **same** ID system where possible. Cross-system
mapping (ENSG↔symbol↔Entrez) is permitted via a standard 1:1 mapping table when
the systems differ; ambiguous / many-to-many genes are dropped (not counted as
overlap).

**Overlap-rate metric**:
```
overlap_rate = | mapped_candidate_genes ∩ reference_genes | / | reference_genes |
```
where `reference_genes` is 1.0's expression feature set (the ENSG / symbol gene
universe of `PDGC_count_matrix.txt`).

**Threshold (newly defined): `overlap_rate >= 0.70`** to qualify as "alignable to
1.0's feature space" for `strict`. Rationale: requiring full identity is
impractical across platforms; 0.70 ensures the bulk of 1.0's modeled genes are
recoverable while rejecting datasets that merely share a handful of genes. This
threshold is a tunable knob (`QUALIFY_GENE_OVERLAP_THRESHOLD`, default `0.70`)
and is **explicitly a qualify-stage decision, not a 1.0 rule**.

**Reference gene set is REQUIRED for `strict`.** The overlap test is only
meaningful when a concrete `reference_genes` set is injected (the runner supplies
it as `{REFERENCE_GENE_SET}`). When **no reference gene set is provided**,
`overlap_rate` cannot be computed deterministically — the gene-overlap gate is
therefore UNSATISFIED, and the verdict is **CAPPED at `loose`** (it can never be
`strict`). The prompt must NOT estimate alignability from ID system or gene count
in this case: doing so is nondeterministic and would contradict the 0.70
gene-overlap policy. `strict` requires an injected reference gene set.

---

## 7. Verdict definitions

| Verdict | Condition |
|---|---|
| **strict** (training-set grade) | Has expression + drug-response + linkable by cell-line ID (§§1,4) **AND** a reference gene set was injected so that expression alignability is computed deterministically — `overlap_rate >= 0.70` after gene-ID mapping (§6); with NO reference gene set the verdict is capped at `loose` — **AND** the dataset's drugs are a subset of the 1.0 compound list under lowercase exact match (§5). |
| **loose** (test-set grade) | Has expression + drug-response (AUC or equivalent) + linkable by cell-line ID (§§1,4). Drugs need **not** overlap the 1.0 compound list, and the gene-overlap threshold need not be met. |
| **fail** | Missing expression, OR missing drug response, OR expression and response not linkable (empty normalized cell-line intersection, §4). |

`strict ⊂ loose` by construction: anything strict also satisfies loose; the extra
gates (gene overlap + drug subset) are what promote loose → strict.

---

## 8. Sanity-check expectation (delivery gate)

The validation prompt is only trusted if it passes a falsifiable floor:

- **Positive control**: feed 1.0's own chenlab data (`PDGC_count_matrix.txt`
  expression + the chenlab drug screen, drugs ⊆ `common_drugs`) ⇒ the verdict
  **MUST be `strict`**. (Ensembl gene IDs overlap 1.0's own reference fully;
  drugs are by definition in the 1.0 list.)
- **Negative control**: feed a known-unrelated dataset (no expression matrix, or
  no drug-response, or non-linkable IDs — e.g. a plain text paper, an image set,
  a proteomics-only table) ⇒ the verdict **MUST be `fail`**.

If the prompt does not satisfy both controls, it is not trustworthy and must be
revised before the qualify runner consumes it. Implementing the runner
that executes these controls is a separate piece of work, out of scope for this spec.

---

## 9. Source citations (1.0 reference repo)

| Rule | File:line |
|---|---|
| Row=cell-line / col=gene contract | `ag/data_source.py:15-21` |
| Drop missing-label rows (`> -inf`) | `ag/data_source.py:40` |
| Missing drug → `np.NINF` | `ag/data_source.py:72-73` |
| ssGSEA `.gct` expression, min-max normalize | `ag/data_source.py:160-168, 217-226` |
| Missing AUC → `-inf` (CTRP read) | `ag/data_source.py:254` |
| AUC pivot, `.fillna("-inf")`, `cpd_name` columns | `ag/ccle_data.py:11-16` |
| CCLE `_`-prefix split | `ag/ccle_data.py:32` |
| `common_idx` intersection join | `ag/ccle_data.py:27,33` |
| chenlab compound row label, `.log2fold.A/B` | `chenlab/data.py:5-13`, `ag/chenlab.py:8,16` |
| `CB-`→`CB`, `-1` suffix, uppercase | `chenlab/data.py:28-35` |
| Normalized-name intersection join | `chenlab/data.py:37` |
| Ensembl gene IDs in raw counts | `ag/chenlab_data/PDGC_count_matrix.txt` (header), `ag/chenlab.py:64-66` |
| SYMBOL variant + `log2(x+1)` | `ag/ssgsea_analysis.py:18,31` |
| `common_drugs` (21 lowercase) | `ag/configs.py:5-28` |
| CTRP `per_compound` (481, `cpd_name`) | `data/v22.meta.per_compound.txt` |
| Gene-ID-mapping policy + 0.70 threshold | **NEWLY DEFINED — §6 of this doc; no 1.0 source** |
