# Bloom Cafe

FastAPI + SQLite + static HTML pages. Deployed on Render's FREE tier
(512 MB RAM, small CPU) at https://coffee-bloom.onrender.com.
Render auto-deploys on every push to `main`.

`api.py` calls `simulate()` at startup, which rebuilds a year of demo orders
ending yesterday (seed 42). Any machine running the code on the same Dallas
date sees the same numbers.

## Hard rules

- **$0 only.** No API keys of any kind (not even free ones), no credit card,
  no paid plans, no paid Render instances.
- **No generative LLM on the Render server.** It won't fit in 512 MB.
  Generative models run only on the GitHub Actions runner (Ollama) or in the
  visitor's own browser (WebLLM, optional).
- **Every number shown to users is computed by code, never by a model.**
  Models only write words, using `{placeholders}` that code fills in.
  Every model output is checked by code, retried with the rejection reason,
  and falls back to a code template. Follow the pattern in `report.py`.
- The Render app must stay well under 512 MB of memory. Measure it after any
  change that adds a dependency or a model.
- Prefer ONNX (fastembed) over PyTorch for anything that runs on Render.

## How to work with the user

- The user is learning. Work in small steps. After each step: stop, show what
  changed and real output, explain it simply, and wait for "continue".
- Don't commit or push unless the user says so.

## Useful commands

- `.venv/bin/python report.py` — morning report (needs Ollama running)
- `.venv/bin/python anomalies.py` — list unusual events
- `.venv/bin/python evaluate.py` — anomaly detector eval
- `.venv/bin/uvicorn api:app --reload` — run the site locally
