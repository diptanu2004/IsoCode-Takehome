# Sample files for the demo

Two partner files to upload on the Uploads screen. They come from the same
generator run as the seeded data (`scripts/generate_data.py`, seed 7, as of
6 Nov 2026), so their orders already exist in the app. Everything in them is
invented.

| File | Upload it as | What it shows |
|---|---|---|
| `marketplace1_settlement_new_format.csv` | Marketplace 1 · Settlement | Marketplace 1 changed its report without notice: a title block above the header, renamed and reordered columns, an extra column and a new date format. The saved layout no longer fits, so Settle asks for a new mapping, with AI suggestions and reasons and a 20-row preview. Approve it and the file loads as a new layout version. |
| `couriera_cod_remittance_late.csv` | Courier A · COD remittance | COD money that arrived after it was due. The open "unpaid past due" claims for these orders close themselves ("Resolved by matching"). |

Uploading the same file twice is recognised, and its rows are not loaded
again. To make full-size files, see `deployment.md` (Sample files).
