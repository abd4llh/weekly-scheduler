# v0.7.0 AI parser patch

Replace these files in the repository root:

- `ai_parser.py`
- `models.py`
- `requirements.txt`

Then add the import/button changes to `app.py` manually, or ask ChatGPT to apply the app patch next.

Streamlit secrets:

```toml
OPENAI_API_KEY = "sk-..."
```

The AI parser function is:

```python
from ai_parser import parse_tasks_with_ai
tasks, warnings = parse_tasks_with_ai(raw_text, api_key, model="gpt-4.1-mini")
```
