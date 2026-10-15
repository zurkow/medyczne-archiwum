## Retrieval & Indexing

### Access filter before retrieval
The RAG pipeline first determines the user's allowed record set, then searches only inside the user's sqlite-vec partition — the partition key is passed directly to the KNN operator, never post-filtered. Returned chunk IDs are rechecked against visible documents; any chunk outside the ACL is rejected and never reaches the prompt. Revoking a grant immediately removes the grantee's index entries.

### Measurements via structured SQL, not vectors
Measurements are never embedded or put in the vector index. They are fetched by a separate ACL-limited, time-bounded SQL query (last 365 days) and passed to the assistant with typed `measurement` citations.

### Validated embeddings and strict structured output
`EmbeddingClient` validates count, dimension (768), finiteness and absence of NaN. Chunks are ~500 units with overlap 80, preserve page boundaries and carry owner, document UUID and page number. Chat requests use a strict JSON Schema `response_format` (not `json_object`).
