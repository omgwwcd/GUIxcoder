"""CLI entry point for GUIxcoder-enhanced WebGen agent.

Runs the iterative web-generation loop and, by default, grounds the VLM
feedback in the CodeGraph via the `guixcoder` package.  Pass
``--baseline`` to disable that grounding and reproduce the original
WebGen-Agent text-only feedback.
"""
import os
import argparse
from pathlib import Path

from agent import WebGenAgent


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run a GUIxcoder / WebGen agent session.")
    p.add_argument("--model", required=True, help="Code-generation LLM (OpenAI-compatible).")
    p.add_argument("--vlm_model", required=True, help="VLM model name (screenshot analysis).")
    p.add_argument("--fb_model", required=True, help="Text model for feedback summarisation.")
    p.add_argument("--instruction", required=True, help="Natural-language website spec.")
    p.add_argument("--workspace-dir", required=True, type=Path,
                   help="Directory where the agent writes the generated project.")
    p.add_argument("--log-dir", required=True, type=Path,
                   help="Directory for per-step logs, screenshots, etc.")
    p.add_argument("--max-iter", type=int, default=20)
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--error-limit", type=int, default=5)

    # Structured-feedback switch.
    fb = p.add_mutually_exclusive_group()
    fb.add_argument("--baseline", dest="use_structured_feedback", action="store_false",
                    help="Disable guixcoder structural feedback (reproduces original WebGen-Agent).")
    fb.add_argument("--structured", dest="use_structured_feedback", action="store_true",
                    help="Enable guixcoder structural feedback (default).")
    p.set_defaults(use_structured_feedback=True)
    return p


def main() -> None:
    args = build_parser().parse_args()

    agent = WebGenAgent(
        model=args.model,
        vlm_model=args.vlm_model,
        fb_model=args.fb_model,
        workspace_dir=str(args.workspace_dir),
        log_dir=str(args.log_dir),
        instruction=args.instruction,
        max_iter=args.max_iter,
        overwrite=args.overwrite,
        error_limit=args.error_limit,
        use_structured_feedback=args.use_structured_feedback,
    )
    mode = "structured (guixcoder)" if args.use_structured_feedback else "baseline (original)"
    print(f"[GUIxcoder] Feedback mode: {mode}")
    agent.run()


if __name__ == "__main__":
    main()
