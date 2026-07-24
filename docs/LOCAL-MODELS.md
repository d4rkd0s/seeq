# Local Models: $0 Tinkering with SeeQ

Running a local language model on your computer lets you hack on SeeQ for **$0 per hour** — no cloud cost, no API keys, complete privacy. Perfect for ham experimenters.

## Why local models?

- **No cost:** Run inference on your GPU, offline
- **Privacy:** Your code, your context, stays on your machine
- **Speed:** No network latency; local edit-compile-test loops are very fast
- **Control:** You pick the model and VRAM tier that fits your hardware

The tradeoff: local models (7B–14B parameters) are less capable than frontier models, but they excel at small, scoped edits in well-commented code.

## Install: Ollama (one-liner)

[Ollama](https://ollama.com) manages local models with a simple CLI. Install:

```bash
curl -fsSL https://ollama.ai/install.sh | sh
```

Then pull a model (examples below by VRAM tier). The first pull is ~5–10 minutes; subsequent runs use the cached model.

```bash
ollama pull qwen2.5-coder:14b   # See "Tier: 12 GB" below
```

Run the model server in the background:

```bash
ollama serve &
```

It listens on `http://localhost:11434` (the default for Claude Code and other tools).

## Model picks by VRAM tier

Choose a model that fits your GPU memory. Typical VRAM usage for inference: ~1.3× the model size (quantized weights + KV cache).

### Tier: 8 GB VRAM

- **[`deepseek-coder:6.7b-base-q5_K_M`](https://ollama.com/library/deepseek-coder)** (~6.7B params, quantized)
  - **VRAM:** ~9–10 GB (fits in 8 GB but tight; use with small context)
  - **Use case:** Quick fixes, small functions, config tweaks
  - Speed: fast on 8 GB GPU

- **[`codeqwen:7b-chat-q5_K_M`](https://ollama.com/library/codeqwen)** (~7B, quantized)
  - **VRAM:** ~9 GB (borderline; works on 8 GB with context limits)
  - **Strengths:** FT8 mode? No, but the code is well-commented Python; handles shell scripts well

### Tier: 12 GB VRAM

- **[`qwen2.5-coder:14b`](https://ollama.com/library/qwen2.5-coder)** — RECOMMENDED (14B params)
  - **VRAM:** ~18–20 GB unquantized; use quantized (Q5): ~13 GB
  - **Instruction:** `ollama pull qwen2.5-coder:14b-instruct-q5_K_M`
  - **Strengths:** Excellent at Python + bash, strong reasoning, good context handling
  - Speed: fast on 12 GB GPU, ~50–100 tokens/sec typical

- **[`mistral:7b`](https://ollama.com/library/mistral)** — If 14B is tight
  - **VRAM:** ~9 GB
  - **Use case:** Lighter-weight alternative; faster but less sophisticated

### Tier: 24+ GB VRAM

- **[`qwen2.5-coder:32b-instruct-q5_K_M`](https://ollama.com/library/qwen2.5-coder)** (32B params, quantized)
  - **VRAM:** ~35 GB
  - **Strengths:** Frontier-class reasoning on code, handles longer context
  - Speed: ~20–50 tokens/sec typical

- **[`deepseek-coder:33b-instruct-q5_K_M`](https://ollama.com/library/deepseek-coder)** (33B params, quantized)
  - **VRAM:** ~36 GB
  - **Strengths:** Deep code understanding, multi-file refactoring
  - Speed: similar to 32B

---

## How to give the model context

Local models work best with explicit system context. Feed them:

1. **The CLAUDE.md file** — project map, safety rules, test commands
2. **The skill file relevant to your task** — e.g., `agents/PREPROMPT.md` for safety, or read the file you're editing

Example: editing `bin/qso.py` to add a config option.

```bash
cat docs/CLAUDE.md agents/PREPROMPT.md bin/qso.py > /tmp/context.txt
```

Then paste `/tmp/context.txt` plus your task into your local-model editor (e.g., Claude Code running against `ollama` as the model, or a local UI like [Ollama Web UI](https://github.com/ollama-webui/ollama-webui)).

In Claude Code CLI (if configured for local):

```bash
# Add to .claude/settings.json:
{
  "model": "ollama:qwen2.5-coder:14b-instruct-q5_K_M"
}
```

Then run Claude Code normally — it will use your local model.

## Safety: TX safety chain is frozen code

**Hard rule:** Local models may **NOT** edit these files:

- `bin/qso.py` — watchdog, TX frequency verification, unkey logic
- `agents/PREPROMPT.md` — safety rules themselves
- Anything in `bin/stop.sh` or the watchdog path

**Why?** These files enforce the legal and safety invariants (FCC Part 97, control-operator responsibility) that keep the station from:

- Transmitting on the wrong frequency
- Keying when the operator walks away
- Sending malformed or double-encoded frames

**What you CAN edit with a local model:**

- Config defaults in `bin/qso.py` (SNR floor, patience, split size, etc.)
- Dashboard (`bin/dashboard.py`) — widgets, colors, displays
- New analysis tools
- `station.conf` and `.example` file itself

### Before commit: always run `make test`

Every patch, no matter how small, must pass:

```bash
make test
```

This runs:

- Python syntax check (`py_compile`) on all `.py` files
- Bash syntax check (`bash -n`) on all `.sh` files and `bin/seeq`
- The unit test suite (`tools/test_sequencer.py`)

If a local model wrote a patch and a test fails, **do not commit.** Fix the error, review the fix (don't re-feed it to the local model — re-read the broken code and understand what went wrong), and re-run `make test`.

## Example: adding a config option

**Task:** Add a `SNR_FLOOR` tuning option to `station.conf`.

**Setup:**

```bash
cd ~/Radio/ft8-claude
ollama pull qwen2.5-coder:14b-instruct-q5_K_M
```

**Feed context to the model:**

```bash
cat docs/CLAUDE.md agents/PREPROMPT.md bin/qso.py > /tmp/ctx.txt
# Then paste /tmp/ctx.txt into your local-model editor along with:
# "Add SNR_FLOOR config option to station.conf; load it in bin/qso.py parse_config()."
```

**After the model writes a patch:**

```bash
git diff
make test              # Must pass
```

If tests pass, commit and you're done. If not, **read the test output**, identify the bug in the model's code, and fix it manually (don't loop the model).

---

## Tools to run local models

- **Claude Code CLI:** Configure `model: ollama:qwen2.5-coder:14b-instruct-q5_K_M` in `.claude/settings.json`; then run `claude code` normally
- **Ollama Web UI:** [`ollama-webui/ollama-webui`](https://github.com/ollama-webui/ollama-webui) — chat interface in your browser
- **Text editor + local model:** Use a plug-in (e.g., Codeium, Continue.dev) that talks to a local Ollama server
- **LM Studio:** GUI for Ollama-compatible models (Mac/Windows/Linux)
- **curl / raw API:** Ollama exposes `/api/generate` (streaming) and `/api/chat` for custom integrations

## Recommended workflow

1. **Start a fresh session per task.** "Add a config option" is one session; "fix a parsing bug" is another.
2. **Keep context in files, not chat.** Paste CLAUDE.md, the PREPROMPT, and the files you're editing into the model's system prompt.
3. **Run `make test` before commit.** No exceptions.
4. **Review the diff carefully.** Even great models sometimes make small mistakes in context (off-by-one, typo in a variable name). Read what it wrote.
5. **Don't loop the model on test failures.** If a test fails, debug it yourself — understand what went wrong, fix it by hand, re-run `make test`. A model trying to fix its own mistake often compounds the error.

## Cost comparison

| Path | Cost per hour | Setup | Speed |
|------|---|---|---|
| **Local (on 12 GB GPU)** | $0 (electricity ≈ $0.01) | 10 min install, model fits | ~50 tok/sec |
| **Haiku (hosted)** | ~$1 (at 0.1 k tokens avg session) | None (API key) | Instant network latency |
| **No AI** | $0 | None | You, thinking |

For a 2-hour evening session with a local model, expect to run 3–5 focused edits on a 12 GB GPU; a local session costs your electricity (roughly a dime). Same edits with hosted Haiku cost ~$2–5. For maintenance work (the common case), the local model + test gate is fastest and cheapest.

---

## Troubleshooting

**"ModuleNotFoundError: No module named 'numpy'"**
Your Python environment is missing numpy. Install:
```bash
pip install numpy
```
Or via your distro:
```bash
sudo apt-get install python3-numpy
```

**"Ollama model not found"**
Ollama server isn't running, or the model isn't pulled. Check:
```bash
ollama list
ollama serve &     # Start the server if not running
ollama pull qwen2.5-coder:14b-instruct-q5_K_M
```

**"CUDA out of memory"**
Your GPU VRAM can't hold the model. Use a smaller quantization (Q4 instead of Q5) or a smaller model (7B instead of 14B). See the tier table above.

**"Model is very slow (< 10 tokens/sec)"**
You may be running CPU inference (no GPU). Check:
```bash
ollama list -v
```
If it says "cpu", you need to [install GPU support](https://github.com/ollama/ollama#gpu-support).

---

## Further reading

- [Ollama docs](https://ollama.com)
- [Qwen2.5-Coder model card](https://huggingface.co/Qwen/Qwen2.5-Coder-32B-Instruct)
- [Deepseek-Coder model card](https://huggingface.co/deepseek-ai/deepseek-coder-33b-instruct)
- SeeQ [docs/COST.md](COST.md) — why the project was built to have $0 runtime cost
- SeeQ [agents/PREPROMPT.md](../agents/PREPROMPT.md) — safety rules that every agent reads
