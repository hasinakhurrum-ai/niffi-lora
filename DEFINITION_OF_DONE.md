# Definition of Done (Cursor checklist)

**DONE** means:

- [ ] `python main.py` runs locally with Ollama running.
- [ ] If model missing, it auto-pulls it.
- [ ] Each iteration: prompts Ollama -> receives code -> runs safely -> logs result.
- [ ] `memory.json` persists `best_score` and `best_code`.
- [ ] `history/` contains all generated candidates.
- [ ] Code-only enforcement exists (reject/clean markdown, ensure valid Python).
- [ ] Sandbox has timeout + output truncation + temp isolation.
