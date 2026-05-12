# Agent Rules

- Work in small vertical slices.
- Keep PLAN.md updated.
- Run tests and lint after each slice.
- If commands fail, inspect logs/app.log and fix before continuing.
- Add logging to logs/app.log.
- Never add real-money trading execution.
- Never commit secrets.
- Use dry-run mode by default.
- Prefer deterministic Python logic for detection.
- Use provider abstractions for data sources.
- Hermes Agent may be added later as an optional analyst/reporting layer, but detection must remain deterministic in Python.
- The repository name is fuck-inside-traders, but the Python package must be fuck_inside_traders because Python imports cannot use hyphens.
- Do not change global Git config; only local repo Git config is allowed.
