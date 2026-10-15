## Answer Contract

### Citations built and validated by the app
Cited IDs returned by the model must be a subset of the source IDs given in the prompt. Displayed sources are built from the DB by the app, never from model text; invalid or unknown IDs are never shown. Citations link to the document page or measurement entry.

### Document content is untrusted input
Document text, OCR output and measurement notes are untrusted data, not instructions. They cannot instruct the model or grant access to tools or data outside the context; `prompt.py` frames them as quoted material.

### Context-only answers, explicit no-data
Answer in Polish only from retrieved context; never fill gaps with general knowledge. Below the configured relevance threshold, or with no sources, return an explicit no-data answer without calling the model. Invalid JSON, foreign IDs, missing model or timeout yield an explicit error — never a substitute answer. No automatic generation retries.

### No diagnosis or clinical interpretation
The assistant and app never diagnose, assess urgency, recommend treatment or medication changes, convert units or interpret trends clinically; they only quote recorded values and dates.
