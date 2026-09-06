"""
agents/run_logger.py — One markdown transcript per user query.

Usage pattern: graph.py's invoke() creates a RunLogger and sets it as the
active logger via the `current_logger` ContextVar. Any node can then do
`logger = current_logger.get()` and call methods on it — no need to thread
a logger object through every Command/state dict.
"""

from __future__ import annotations
import contextvars
from datetime import datetime
from pathlib import Path
import json

LOG_DIR = Path("detail_logs")
LOG_DIR.mkdir(exist_ok=True)

_active_logger: "RunLogger | None" = None

class current_logger:
    """Drop-in replacement for the old ContextVar accessor, backed by a
    plain global instead of contextvars.ContextVar.

    LangGraph runs each node in its own worker thread — ContextVar state
    does not cross threads, which is why only the node that created the
    logger ever saw it (every other node's thread read a blank context and
    silently skipped logging). A plain global is visible from every thread
    since they share process memory, which is what a single local CLI
    session needs. (Not safe for concurrent multi-user serving — not this
    use case.)
    """
    @staticmethod
    def get():
        return _active_logger

    @staticmethod
    def set(logger):
        global _active_logger
        _active_logger = logger


class RunLogger:
    def __init__(self, thread_id: str, user_message: str):
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.path = LOG_DIR / f"{ts}_{thread_id}.md"
        self._write(f"# Run — {ts}\n\n**User:** {user_message}\n")

    def _write(self, text: str):
        with self.path.open("a", encoding="utf-8") as f:
            f.write(text + "\n")

    def plan(self, plan: list[dict]):
        lines = "\n".join(f"{i+1}. **{s['agent']}** — {s['instruction']}" for i, s in enumerate(plan))
        self._write(f"## Plan\n\n{lines}\n")

    def step(self, agent: str, task: str, result: str):
        self._write(f"## Step: `{agent}`\n\n**Task:**\n```\n{task}\n```\n\n**Result:**\n```\n{result}\n```\n")

    def judge(self, agent: str, verdict: str, feedback: str = ""):
        icon = "✅" if verdict == "pass" else "🔁"
        self._write(f"> {icon} **Judge on `{agent}`:** {verdict}" + (f" — {feedback}" if feedback else ""))

    def final(self, message: str):
        self._write(f"## Final Answer\n\n{message}\n")

    def node_update(self, node_name: str, update: dict):
        """Generic per-node logger — called from graph.py's node wrapper after
        every node runs, regardless of entry point (invoke() or raw app.invoke())."""
        if not update:
            return
        if node_name == "supervisor" and update.get("plan"):
            self.plan(update["plan"])
            return

        skip_keys = {"messages", "judge_target", "retry_count", "next"}
        lines = []
        for key, value in update.items():
            if key in skip_keys or value in (None, ""):
                continue
            if isinstance(value, (list, dict)):
                value = json.dumps(value, indent=2, ensure_ascii=False, default=str)
            lines.append(f"**{key}:**\n```\n{value}\n```")

        if lines:
            self._write(f"## Node: `{node_name}`\n\n" + "\n\n".join(lines) + "\n")