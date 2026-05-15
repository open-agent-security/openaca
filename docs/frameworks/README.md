# Framework References

These files are curated policy inputs for OpenACA overlay review and future
LLM-assisted candidate annotation. They intentionally summarize the
frameworks instead of mirroring full upstream documents.

Use them as stable prompt context when drafting:

- `database_specific.openaca.taxonomies`
- `database_specific.openaca.threat_kind`

Rules for updates:

- Keep source URLs and access dates current.
- Prefer concise mapping guidance over long copied text.
- Treat changes as review-worthy because they can change corpus
  classification behavior.
- Do not duplicate upstream-owned CWE mappings into OpenACA overlays by
  default; use `supplemental_taxonomies` only for reviewed OpenACA-owned
  additions.

Overlay taxonomy IDs use lowercase prefixes where the schema expects
them, for example `asi04`, `mcp04:2025`, `ast02:2026`, and
`llm03:2025`.
