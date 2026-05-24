# Default Literature Digest Configuration

Use these defaults when the user asks for a starter daily literature digest.

## Schedule And Delivery

- Frequency: daily
- Time: `09:00`
- Timezone: ask the user; if unclear, use the user's local timezone.
- Recipient: ask the user.
- Email connector: Gmail when connected.
- Local archive:
  - `daily-literature-digests/YYYY-MM-DD.md`
  - `daily-literature-digests/YYYY-MM-DD.docx` only when requested
  - `daily-literature-digests/fulltext-inbox/to-download-YYYY-MM-DD.md`
  - `daily-literature-digests/fulltext-summaries/`
  - `daily-literature-digests/data/YYYY-MM-DDTHHMMSSZ.json`
  - `daily-literature-digests/state.json`

## Default Config Shape

```json
{
  "recipient_email": "user@example.com",
  "crossref_mailto": "user@example.com",
  "language": "zh-CN",
  "timezone": "Europe/London",
  "schedule_time": "09:00",
  "output_dir": "daily-literature-digests",
  "include_arxiv": true,
  "rows": 20,
  "arxiv_rows": 25,
  "max_papers": 30,
  "keyword_groups": [
    {
      "label": "multi-objective optimization",
      "terms": [
        "multi-objective optimization",
        "multi objective optimization",
        "multiobjective optimization"
      ]
    },
    {
      "label": "surrogate-assisted optimization",
      "terms": [
        "surrogate-assisted optimization",
        "surrogate assisted optimization",
        "surrogate model optimization",
        "surrogate models"
      ]
    },
    {
      "label": "reinforcement learning",
      "terms": [
        "reinforcement learning",
        "deep reinforcement learning",
        "multi-agent reinforcement learning"
      ]
    },
    {
      "label": "building design/operation",
      "terms": [
        "building design",
        "building operation",
        "building energy",
        "building performance",
        "HVAC"
      ]
    }
  ]
}
```

## Default Sources

If the user does not customize publishers, omit the `publishers` key and the script will use:

- Elsevier through Crossref member `78`; uses Crossref created-date because some records have future issue dates.
- Springer Nature through Crossref member `297`.
- Wiley through Crossref member `311`.
- Taylor & Francis / Routledge through Crossref member `301`.
- arXiv through `https://export.arxiv.org/api/query`.
- OpenAlex enrichment by DOI when available.

arXiv results must be labeled as preprints.

## Interpretation Rules

- Summarize from title, abstract, keywords, subjects, and metadata only.
- If the abstract is unavailable, include the paper as a title-only candidate when the title or query term matches the user's keywords.
- Clearly state that no abstract/full text was read for title-only candidates.
- Do not infer goal, method, or result without an abstract or explicit full text.
- Create a separate follow-up list for title-only candidates. The user logs in themselves; Codex can then process that explicit batch using the active session or local PDFs.
- Do not auto-login to school libraries or publisher websites.
