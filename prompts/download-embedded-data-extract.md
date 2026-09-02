<!-- Auto-extracted from `_LLM_EXTRACT_PROMPT` in download/embedded_data_team.py (line 34).
     This is a READ-ONLY COPY for reading and citation; the code is
     authoritative at runtime. -->

# _LLM_EXTRACT_PROMPT

Source: `download/embedded_data_team.py` L34

---

Extract the DRUG-RESPONSE (drug-sensitivity) data from this paper into a single CSV matrix. Drug response means per-sample/per-cell-line drug sensitivity metrics: IC50, EC50, GI50, AUC, viability, or %inhibition.

The data may appear ONLY in prose (e.g. 'IC50 was 2.5 µM for drug X in cell line A') or in FIGURES (dose-response curves, IC50 bar charts, heatmaps) — extract from the text AND the attached figure images.

Output rules (obey EXACTLY):
- First row = header: the first column is the sample/cell-line identifier, the remaining columns are drug names.
- One row per sample/cell line; cells are the numeric metric value.
- Include a final commented line `# metric: <IC50|AUC|...> unit: <unit>` describing what the numbers are.
- Output ONLY raw CSV (no prose, no code fences).
- If the paper contains NO per-sample drug-response values in text or figures, output exactly: NONE

