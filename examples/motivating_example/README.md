# Motivating Example — baseline vs. guixcoder-enhanced feedback

This folder is the canonical reproducible experiment referenced in the
top-level README.  Run both commands on the same machine, compare the
final workspaces + per-step logs, and the benefit of DOM→Code
structured feedback shows up in the iteration count and the final
screenshot grade.

## Instruction

```
Build a small single-page React + Vite site that lets users search a
(hard-coded) stock database by ticker.  When the ticker is not found,
the search results page must render a friendly empty-state message
"No stock found — try AAPL or MSFT" together with a link back to the
home page.  Style: white background, navy accents.
```

## 1. Baseline run — original WebGen-Agent feedback

```bash
python infer.py \
    --model        "$CODE_MODEL" \
    --vlm_model    "$VLM_MODEL" \
    --fb_model     "$FB_MODEL" \
    --instruction  "$(cat examples/motivating_example/instruction.txt)" \
    --workspace-dir ./runs/baseline/workspace \
    --log-dir       ./runs/baseline/logs \
    --max-iter 20 \
    --baseline
```

The VLM reports the symptom in plain text ("red error box", "page is
blank after search") but gives no location.  Expected behaviour: the
code LLM edits the wrong file more than once, hits `--error-limit 5`,
backtracks, and usually fails to finish within 20 iterations.

## 2. Enhanced run — guixcoder structured feedback

```bash
python infer.py \
    --model        "$CODE_MODEL" \
    --vlm_model    "$VLM_MODEL" \
    --fb_model     "$FB_MODEL" \
    --instruction  "$(cat examples/motivating_example/instruction.txt)" \
    --workspace-dir ./runs/structured/workspace \
    --log-dir       ./runs/structured/logs \
    --max-iter 20 \
    --structured
```

Now every turn's feedback string is appended with a *Structural Analysis*
block such as:

```
## Structural Analysis (guixcoder)

- Anomaly: error text "No stock found" rendered in search results
  → Code: src/pages/SearchResultPage.jsx:82-84
  Snippet:
    {notFound && (
      <div className="no-result">{errorText}</div>
    )}
```

Expected behaviour: the code LLM receives the exact JSX node on the
first failure, fixes it in one edit, passes the screenshot grade on
iteration 2-3, and emits `boltAction type="finish"`.

## 3. What to look at

| Signal | Where |
|---|---|
| Per-step screenshot grade | `runs/{mode}/logs/stepN.json` → `screenshot_grade` |
| Whether the run finished | `runs/{mode}/logs/stepN.json` → `messages[-1].info.is_finish` |
| Total iterations | highest `N` in `runs/{mode}/logs/stepN.json` |
| Feedback text diff | compare `messages[-2].content` across the two runs |

The expected delta matches the diagram in
`docs/figures/iteration_comparison.svg`.

## 4. Why this counts as a *motivating* example

A full benchmark would need dozens of annotated (symptom → ground-truth
code location) pairs across many projects — tracked in the roadmap.
A single end-to-end comparison on one instruction is enough to show
that grounding the VLM feedback in the CodeGraph removes an entire
class of failure modes (wrong-file edits), which is the central claim
of GUIxcoder.
