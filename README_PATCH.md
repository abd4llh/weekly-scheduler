# Weekly Scheduler v0.8.0 patch

Replace these files in the repo root:

- `models.py`
- `ai_parser.py`

This adds:

- AI-first task parsing for bullets and paragraphs
- generic consistency validation
- one AI self-repair pass
- confidence / assumptions / clarification fields

Your Streamlit secrets still need:

```toml
OPENAI_API_KEY = "sk-..."
```
