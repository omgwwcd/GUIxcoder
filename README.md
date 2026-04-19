# GUIxcoder

> Web-generation agent that grounds VLM feedback in the source code —
> the next-turn LLM gets **`→ Page.jsx:82-84`** instead of *"red error box"*.

<p align="center">
  <img src="docs/figures/architecture.svg" width="100%" alt="GUIxcoder architecture">
</p>

Built on the [WebGen-Agent](https://arxiv.org/abs/2509.22644) iterative loop.
Only the feedback channel changes: a DOM→Code alignment step pins every
VLM complaint to `file:line:snippet`. Everything else (prompts,
backtracking, step persistence) is untouched — gains come from the
feedback, not the scaffolding.

## Quickstart

```bash
git clone https://github.com/omgwwcd/GUIxcoder.git
cd GUIxcoder && pip install -e .
cp .env.example .env     # fill OpenAI-compatible endpoints
```

`--model` / `--vlm_model` / `--fb_model` accept any OpenAI-compatible
endpoint (OpenAI, OpenRouter, ModelScope, local Ollama, …).

```bash
# baseline — text-only VLM feedback
python infer.py --instruction "$(cat examples/motivating_example/instruction.txt)" \
    --workspace-dir ./runs/baseline --log-dir ./runs/baseline/logs --baseline

# GUIxcoder — DOM→Code grounded feedback
python infer.py --instruction "$(cat examples/motivating_example/instruction.txt)" \
    --workspace-dir ./runs/structured --log-dir ./runs/structured/logs --structured
```

Full walkthrough: [`examples/motivating_example/README.md`](examples/motivating_example/README.md).

## Why it works

<p align="center">
  <img src="docs/figures/iteration_comparison.svg" width="100%" alt="Baseline vs GUIxcoder iterations">
</p>

Same LLM / VLM / budget. Baseline stalls because the VLM only says
*what* is wrong; the LLM guesses *which file*. `--structured` appends
`file:line:snippet` each turn, loop converges in a few iterations.

## API

```python
from guixcoder import FeedbackEnhancer

r = FeedbackEnhancer(workspace_dir="./my-app").enhance(
    vlm_feedback="red 'No stock found' error box",
    url="http://localhost:5173/search?q=INVALID",
)
print(r.formatted_text)          # VLM text + → file:line + snippet
for loc in r.code_locations:
    print(f"{loc.file}:{loc.line_start}-{loc.line_end}")
```

Three replaceable components: **CodeGraph** (tree-sitter AST),
**DOM extractor** (headless Chromium), **Aligner** (rule-based shipped,
contrastive dual-tower stubbed for Phase 2).

## Related work

- [**WebGen-Agent**](https://arxiv.org/abs/2509.22644) — we replace its feedback channel.
- [**Waffle** (ACL 2025)](https://arxiv.org/abs/2410.18362) — structure-aware UI→HTML; same alignment spirit, but for *generation*; we target *feedback*.
- [**ScreenCoder**](https://arxiv.org/abs/2507.22827) — modular multi-agent visual-to-code; inspires layered feedback.

## License

MIT. Builds on WebGen-Agent and Waffle — cite the originals when
building on their work.
