# Selective risk threshold refinement

Lowering only the `biohazard_red` and `illegible_biometrics` visible-OCR
thresholds from 0.60 to 0.55 had positive grouped recovery evidence. The
change cannot create an approval: it only adds extracted risk evidence, and a
disqualifying flag may only move review to denial.

The complete offline Docker evaluation scored `131.980301 / 150`, a
`+0.198741` gain from the prior checkpoint. Extraction rose to `45.703333`,
classification to `69.660000`, calibration to `16.616968`, and catastrophic
false approvals remained zero. All 57 unit tests passed.
