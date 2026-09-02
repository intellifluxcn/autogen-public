<!-- Auto-extracted from `_PRESCREEN_USER_TEMPLATE` in find/prescreen.py (line 78).
     This is a READ-ONLY COPY for reading and citation; the code is
     authoritative at runtime. -->

# _PRESCREEN_USER_TEMPLATE

Source: `find/prescreen.py` L78

---

Score this paper from 0 to 10. DRUG-RESPONSE DATA IS THE GATE: this pipeline already has abundant expression/omics; what is scarce is drug-response measurements on in-vitro tumor models. A paper WITHOUT any drug-treatment / drug-response signal is useless here no matter how rich its sequencing.

Three ingredients:
1. IN-VITRO TUMOR MODEL (patient-derived or established; NOT in-vivo-only or clinical-trial-only). Any of: organoid / tumor organoid / PDO; cancer or tumor cell line, cell strain, patient-derived cells, primary tumor culture; cancer/tumor/glioma stem(-like) cells (CSC / GSC / TIC); tumorsphere / neurosphere / spheroid; or similar in-vitro models. Do NOT reward only organoids — all model types above count equally.
2. [GATE] DRUG RESPONSE on those models — a quantitative readout (IC50, AUC, GR metrics, viability, dose-response, drug-sensitivity / DSS score, inhibition rate). Drug names may be abbreviations, brand names or compound codes (e.g. TMZ, Velcade, ZD6474) — do not penalize that. Multiple drugs (a panel) are better than one, but a single drug across many models still counts.
3. TRANSCRIPTOME / SEQUENCING on the SAME models: RNA-seq, transcriptomic profiling, gene-expression matrix, microarray.

Scoring (the drug-response gate is FIRM — never score >=3 for a paper with zero drug-response signal):
  0-2  = NO drug-response signal of any kind — score HERE EVEN IF rich sequencing/expression is described (expression-only, omics-only, review, methods-only, or clinical-outcome-only with no in-vitro drug assay)
  3-4  = drug response present but weak/unusable: NOT on an in-vitro model (e.g. clinical response only), OR purely qualitative with no quantitative readout
  5-7  = quantitative drug response on in-vitro models (>=1 drug); expression / sequencing plausibly present
  8-10 = paired quantitative drug response + expression on the SAME in-vitro models (matched / same-sample), enabling within-sample drug-sensitivity ranking

Respond ONLY with compact JSON: {{"score": <number 0-10>, "reason": "<short phrase>"}}.

TITLE: {title}

ABSTRACT: {abstract}
