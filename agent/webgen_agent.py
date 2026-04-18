"""WebGen-style agent with pluggable structured feedback.

The agent is a minimal port of the WebGen-Agent iterative loop
(LLM -> files -> npm dev -> screenshot -> VLM -> feedback -> next turn).
The *only* difference from the original is the feedback path: when
`use_structured_feedback=True`, the agent calls the `guixcoder` package
to ground VLM feedback in the CodeGraph so the next-turn prompt tells
the LLM exactly which file:line to look at.
"""
import os
import json
import shutil
import time
import re
from typing import Dict, Tuple, Any

from .prompts import system_prompt, reminders_prompt
from .utils import (
    llm_generation,
    extract_and_write_files,
    execute_for_feedback,
    execute_for_webvoyager_feedback,
    get_screenshot_description,
    get_screenshot_grade,
    get_screenshot_description_with_context,
    generate_gui_agent_instruction,
    directory_to_dict,
    dict_to_directory,
    restore_from_last_step,
)

# Structured-feedback path — from the standalone guixcoder package.
from guixcoder import FeedbackEnhancer
from guixcoder.code_graph import build_code_graph, graph_to_context
from guixcoder.dom_extractor import CLIDOMExtractor


def remove_dir(directory):
    for _ in range(5):
        try:
            shutil.rmtree(directory)
            return True
        except Exception:
            time.sleep(5)
    return False


class WebGenAgent:
    def __init__(
        self,
        model: str,
        vlm_model: str,
        fb_model: str,
        workspace_dir: str,
        log_dir: str,
        instruction: str,
        max_iter: int,
        overwrite: bool,
        error_limit: int,
        max_tokens: int = -1,
        max_completion_tokens: int = -1,
        temperature: float = 0.5,
        use_structured_feedback: bool = True,
    ) -> None:
        self.model = model
        self.vlm_model = vlm_model
        self.fb_model = fb_model
        self.max_tokens = max_tokens
        self.max_completion_tokens = max_completion_tokens
        self.temperature = temperature
        self.use_structured_feedback = use_structured_feedback

        if os.path.exists(workspace_dir):
            remove_dir(workspace_dir)
        os.makedirs(workspace_dir)
        if overwrite:
            if os.path.exists(log_dir):
                remove_dir(log_dir)
            os.makedirs(log_dir)
        else:
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)

        self.id = os.path.basename(log_dir)
        self.is_finished = False
        messages, gui_instruction, step_idx, screenshot_grade, webvoyager_grade, nodes = \
            restore_from_last_step(log_dir, workspace_dir, max_iter)
        self.workspace_dir = workspace_dir
        self.log_dir = log_dir

        if messages is not None and len(messages) > 0:
            self.messages = messages
            self.gui_instruction = gui_instruction
            if messages[-1].get("info", {}).get("is_finish", False):
                self.is_finished = True
            self.step_idx = step_idx
            print(f"[{self.id}] Resuming from step {step_idx}")
            self.screenshot_grade, self.webvoyager_grade = screenshot_grade, webvoyager_grade
            self.nodes = nodes
            self.pre = step_idx
            self.error_count = self.get_error_count(f"step{step_idx}.json")
        else:
            self.messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": instruction},
            ]
            # GUI instruction generation is optional — stub it if fb_model unavailable.
            try:
                self.gui_instruction = generate_gui_agent_instruction(
                    instruction, fb_model, self.max_tokens, self.max_completion_tokens
                )
            except Exception as e:
                print(f"[{self.id}] gui_instruction generation failed ({e}); skipping.")
                self.gui_instruction = None
            self.pre = -1
            self.step_idx = -1
            self.screenshot_grade, self.webvoyager_grade = 0, 0
            self.nodes = {}
            self.error_count = 0

        self.instruction = instruction
        self.max_iter = max_iter
        self.error_limit = error_limit
        self.restart_limit = 5

        # Structured-feedback components (only used when flag is on).
        self.enhancer = None
        self.code_graph = None
        self.code_structure_context = ""
        self.cli_extractor = None
        if self.use_structured_feedback:
            self.enhancer = FeedbackEnhancer(workspace_dir=self.workspace_dir)
            self.cli_extractor = CLIDOMExtractor(self.workspace_dir)
            self._update_code_graph()

    def _update_code_graph(self):
        if not self.use_structured_feedback:
            return
        self.code_graph = build_code_graph(self.workspace_dir)
        self.code_structure_context = graph_to_context(self.code_graph)
        if self.enhancer:
            self.enhancer.refresh_code_graph()

    def get_concise_messages(self):
        return [{"role": m["role"], "content": m["content"]} for m in self.messages]

    def _get_dom_insights(self) -> Dict:
        if not self.cli_extractor or not getattr(self.cli_extractor, "chrome_path", None):
            return {}
        try:
            log_file = os.path.join(self.log_dir, "service.log")
            if not os.path.exists(log_file):
                return {}
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            url_match = re.search(r"http://(?:localhost|\d+\.\d+\.\d+\.\d+):\d+/?", content)
            if url_match:
                return self.cli_extractor.get_page_insights(url_match.group(0))
        except Exception as e:
            print(f"[WARN] DOM extraction failed: {e}")
        return {}

    def _get_current_url(self) -> str:
        try:
            log_file = os.path.join(self.log_dir, "service.log")
            if os.path.exists(log_file):
                with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                url_match = re.search(r"http://(?:localhost|\d+\.\d+\.\d+\.\d+):\d+/?[^\s]*", content)
                if url_match:
                    return url_match.group(0)
        except Exception:
            pass
        return "http://localhost:5173/"

    def _enhance_feedback_with_guixcoder(self, vlm_feedback: str) -> str:
        """Wrap raw VLM text with guixcoder's DOM->Code alignment block."""
        if not self.use_structured_feedback or not self.enhancer:
            return ""
        try:
            url = self._get_current_url()
            result = self.enhancer.enhance(vlm_feedback=vlm_feedback, url=url)
            return result.structural_analysis or ""
        except Exception as e:
            print(f"[WARN] guixcoder enhancement failed: {e}")
            return ""

    def get_feedback(self, cmds, start_cmd, step_idx, is_webvoyager=False, gui_instruction=None):
        if is_webvoyager:
            feedback = execute_for_webvoyager_feedback(
                gui_instruction, self.workspace_dir, self.log_dir,
                self.vlm_model, self.fb_model, cmds, start_cmd, step_idx,
            )
        else:
            feedback = execute_for_feedback(self.workspace_dir, self.log_dir, cmds, start_cmd, step_idx)

        output = []
        suffix = []
        error_stages = []
        has_error = False
        vlm_text_for_enhance = ""

        install_error = feedback["install_error"]
        if len(install_error) > 0:
            has_error = True
            error_stages.append("dependency installation")
            output.append("**Installation of dependencies emitted errors:**\n\n" + "\n\n".join(
                [f"> {cmd}\n{o}" for cmd, o in feedback["install_results"]]))
            if len(install_error) == 1:
                suffix.append(f"Execution of command `{install_error[0]}` has failed.")
            else:
                suffix.append("Execution of commands "
                              + ", ".join([f"`{e}`" for e in install_error][:-1])
                              + f" and `{install_error[-1]}` have failed.")
        else:
            output.append("Installation of dependencies was successful.")

        if feedback["start_error"]:
            has_error = True
            error_stages.append("service starting")
            output.append(f"**Starting the service emitted errors:**\n\n{feedback['start_results']}")
            suffix.append("The service emitted errors when it was being started.")
        else:
            output.append("Starting the service was successful.")

        if is_webvoyager:
            if len(feedback["webvoyager_error"]) > 0:
                webvoyager_grade = 0
                output.append(f"There was an error when navigating the website with the GUI agent:\n\n{feedback['webvoyager_error']}")
                has_error = True
                error_stages.append("GUI agent navigation")
            else:
                wv = feedback["webvoyager_feedback"]
                webvoyager_grade = wv.get("grade", 0) if isinstance(wv, dict) else 0
                if isinstance(wv, dict) and "improvement_suggestions" in wv:
                    if wv["improvement_suggestions"]:
                        output.append(f"**The suggestions based on the GUI-agent testing result:**\n\n{wv['improvement_suggestions']}")
                    else:
                        output.append("The GUI agent testing is successful and no further improvement is necessary.")
                else:
                    output.append("GUI agent testing is not available in this build (WebVoyager stub).")
            self.webvoyager_grade = webvoyager_grade
        else:
            screenshot_description = None
            screenshot_grade_json, screenshot_grade = None, 0
            if len(feedback["screenshot_error"]) > 0:
                output.append(f"There was an error when getting the screenshot of the started website:\n\n{feedback['screenshot_error']}")
                has_error = True
                error_stages.append("screenshot collection")
            elif os.path.isfile(feedback["screenshot_path"]):
                if self.use_structured_feedback:
                    dom_insights = self._get_dom_insights() or None
                    screenshot_description = get_screenshot_description_with_context(
                        feedback["screenshot_path"],
                        self.vlm_model,
                        code_structure_context=self.code_structure_context,
                        dom_insights=dom_insights,
                    )
                else:
                    screenshot_description = get_screenshot_description(
                        feedback["screenshot_path"], self.vlm_model
                    )
                screenshot_grade_json, screenshot_grade = get_screenshot_grade(
                    feedback["screenshot_path"], self.vlm_model, self.instruction
                )
                self.screenshot_grade = screenshot_grade

                ok = False
                if screenshot_description.get("error_message"):
                    output.append(f"**The screenshot contains errors:**\n\n{screenshot_description['error_message']}")
                    vlm_text_for_enhance += screenshot_description["error_message"] + "\n"
                    has_error = True
                    error_stages.append("screenshot")
                    ok = True
                if screenshot_description.get("screenshot_description"):
                    output.append(f"**The screenshot description:**\n\n{screenshot_description['screenshot_description']}")
                    vlm_text_for_enhance += screenshot_description["screenshot_description"] + "\n"
                    ok = True
                if screenshot_description.get("suggestions"):
                    output.append(f"**Suggestions for Improvement:**\n\n{screenshot_description['suggestions']}")
                    ok = True
                if not ok:
                    output.append("Failed to get screenshot description or screenshot error messages.")
                    has_error = True
                    error_stages.append("screenshot description")

        if has_error:
            suffix.append("Modify the code to fix the errors in " + ", ".join(error_stages) + ".")
        else:
            if is_webvoyager:
                suffix.append("Observe the above feedback and decide whether further modifications to the code are needed based on the GUI-agent testing summary. If no further modification is necessary, output <boltAction type=\"finish\"/> to signal that the task is finished. Otherwise, continue modifying the code until the requirements are fulfilled. IMPORTANT: If you decide to make modifications, do not output the finish signal.")
            else:
                suffix.append("Observe the above feedback and decide whether further modifications to the code are needed based on the screenshot observations. If no further modification is necessary, output <boltAction type=\"screenshot_validated\"/> to signal that the screenshot is satisfactory. Otherwise, continue modifying the code until the requirements are fulfilled. IMPORTANT: If you decide to make modifications, do not output the finish signal.")

        if is_webvoyager:
            info = {"feedback": feedback, "webvoyager_grade": self.webvoyager_grade}
        else:
            info = {"feedback": feedback, "screenshot_description": screenshot_description,
                    "screenshot_grade_json": screenshot_grade_json, "screenshot_grade": self.screenshot_grade}

        feedback_str = "\n\n".join(output) + "\n\n" + "\n".join(suffix)

        # Structural-feedback injection (the guixcoder contribution).
        if self.use_structured_feedback and not is_webvoyager and vlm_text_for_enhance:
            structural = self._enhance_feedback_with_guixcoder(vlm_text_for_enhance)
            if structural:
                feedback_str += "\n\n" + structural

        feedback_str += f"\n\n**The instruction describing the website you are currently developing:**\n\n{self.instruction}\n\n" + reminders_prompt
        return info, feedback_str, has_error

    def get_cmds(self, output):
        return ["npm install"], "npm run dev"

    def step(self, i):
        concise_messages = self.get_concise_messages()
        output = llm_generation(
            concise_messages, self.model,
            max_tokens=self.max_tokens, max_completion_tokens=self.max_completion_tokens,
            temperature=self.temperature,
        )

        info = {}
        has_error = False
        if 'boltAction type="finish"' in output or "boltAction type='finish'" in output:
            info["is_finish"] = True
            self.messages.append({"role": "assistant", "content": output, "info": info})
        elif 'boltAction type="screenshot_validated"' in output or "boltAction type='screenshot_validated'" in output:
            self.messages.append({"role": "assistant", "content": output, "info": info})
            cmds, start_cmd = self.get_cmds(output)
            info, feedback_str, has_error = self.get_feedback(
                cmds, start_cmd, i, is_webvoyager=True, gui_instruction=self.gui_instruction,
            )
            self.messages.append({"role": "user", "content": feedback_str, "info": info})
        else:
            extract_and_write_files(output, self.workspace_dir)
            self._update_code_graph()
            self.screenshot_grade, self.webvoyager_grade = 0, 0
            self.messages.append({"role": "assistant", "content": output, "info": info})
            cmds, start_cmd = self.get_cmds(output)
            info, feedback_str, has_error = self.get_feedback(cmds, start_cmd, i)
            self.messages.append({"role": "user", "content": feedback_str, "info": info})
        return info, has_error

    def _safe_step(self, i):
        try:
            return self.step(i)
        except Exception as e:
            print(f"[{self.id}] Step {i} failed with exception: {e}")
            info = {"error": str(e), "step_exception": True}
            has_error = True
            if self.messages and self.messages[-1].get("role") == "assistant":
                self.messages.append({"role": "user",
                                      "content": f"Step failed with error: {e}. Please continue from the generated code.",
                                      "info": info})
            return info, has_error

    def save_history(self, i, pre=None, has_error=False):
        output_file = os.path.join(self.log_dir, f"step{i}.json")
        if self.screenshot_grade is not None and self.screenshot_grade <= 2:
            has_error = True

        self.nodes[f"step{i}.json"] = {
            "screenshot_grade": self.screenshot_grade,
            "webvoyager_grade": self.webvoyager_grade,
            "pre": pre,
            "has_error": has_error,
        }

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump({
                "messages": self.messages,
                "gui_instruction": self.gui_instruction,
                "files": directory_to_dict(self.workspace_dir),
                "screenshot_grade": self.screenshot_grade,
                "webvoyager_grade": self.webvoyager_grade,
                "pre": pre,
                "nodes": self.nodes,
                "has_error": has_error,
                "use_structured_feedback": self.use_structured_feedback,
            }, f)

        self.error_count = self.error_count + 1 if has_error else 0

    def _extract_step_index(self, filename: str) -> int:
        m = re.search(r"step(\d+)\.json$", filename)
        return int(m.group(1)) if m else -1

    def choose_best_node(self) -> Tuple[str, Dict[str, Any], bool]:
        if not self.nodes:
            return None, None, False

        good = [item for item in self.nodes.items()
                if item[1].get("screenshot_grade", 0) and item[1]["screenshot_grade"] >= 3
                and not item[1].get("has_error", False)]
        candidates = good if good else list(self.nodes.items())
        has_valid = bool(good)

        def rank_key(item):
            _, rec = item
            return (rec.get("webvoyager_grade", -float("inf")),
                    rec.get("screenshot_grade", -float("inf")),
                    self._extract_step_index(item[0]))

        best = max(candidates, key=rank_key)
        return best[0], best[1], has_valid

    def get_error_count(self, file_name):
        count = 0
        curr = self._extract_step_index(file_name)
        while curr != -1 and f"step{curr}.json" in self.nodes:
            if self.nodes[f"step{curr}.json"]["has_error"]:
                count += 1
            else:
                break
            curr = self.nodes[f"step{curr}.json"]["pre"]
        return count

    def run(self):
        errored = False
        restart = False
        restart_num = 0
        if not self.is_finished:
            for i in range(self.step_idx + 1, self.max_iter):
                print(f"[{self.id}] Error count: {self.error_count}")
                if self.error_count >= self.error_limit or restart:
                    print(f"[{self.id}] Backtracking...")
                    file_name, record, has_valid = self.choose_best_node()
                    if has_valid:
                        with open(os.path.join(self.log_dir, file_name), "r", encoding="utf-8") as f:
                            data = json.load(f)
                        dict_to_directory(data["files"], self.workspace_dir)
                        self.messages = data["messages"]
                        self.pre = self._extract_step_index(file_name)
                        self.screenshot_grade = self.nodes[f"step{self.pre}.json"]["screenshot_grade"]
                        self.webvoyager_grade = self.nodes[f"step{self.pre}.json"]["webvoyager_grade"]
                        self.error_count = self.get_error_count(file_name)
                    else:
                        self.messages = [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": self.instruction},
                        ]
                        self.pre = -1
                        self.screenshot_grade, self.webvoyager_grade = 0, 0
                        self.error_count = 0
                        if os.path.exists(self.workspace_dir):
                            remove_dir(self.workspace_dir)
                        os.makedirs(self.workspace_dir)

                print(f"[{self.id}] ==== Step {i} ====")
                info, has_error = self._safe_step(i)
                self.save_history(i, pre=self.pre, has_error=has_error)

                if info.get("step_exception") and not info.get("is_finish"):
                    if restart_num < self.restart_limit:
                        restart_num += 1
                        restart = True
                        time.sleep(5)
                        continue
                    errored = True
                    break
                self.pre = i
                if info.get("is_finish", False):
                    print(f"[{self.id}] Task completed.")
                    self.is_finished = True
                    break

        if not self.is_finished and not errored:
            print(f"[{self.id}] Max iteration reached.")

        file_name, record, has_valid = self.choose_best_node()
        if file_name is None:
            return {"messages": self.messages,
                    "files": directory_to_dict(self.workspace_dir) if os.path.exists(self.workspace_dir) else {},
                    "node": None, "error": "No valid nodes found"}

        with open(os.path.join(self.log_dir, file_name), "r", encoding="utf-8") as f:
            data = json.load(f)
        dict_to_directory(data["files"], self.workspace_dir)
        data["node"] = file_name
        print(f"[{self.id}] Chosen node {file_name}.")
        return data
