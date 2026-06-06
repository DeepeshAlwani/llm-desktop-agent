"""
call_ollama.py  —  Main entry point (updated for LangGraph multi-agent).

WHAT CHANGED vs the original:
  - The single monolithic `agent` is REMOVED.
  - `from agents.graph import get_app` replaces it.
  - `app.invoke({"messages": trimmed})` replaces `agent.invoke(...)`.
  - Everything else — voice, TTS, memory, Rich rendering, keyboard thread —
    is 100% unchanged.

The graph (agents/graph.py) routes automatically:
  - Presentation requests  → ppt_agent_node
  - Everything else        → system_agent_node
"""

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markdown import Markdown
from rich.text import Text
from prompt_toolkit import prompt
from prompt_toolkit.patch_stdout import patch_stdout
import json
import re
import threading
import queue
import memory
import uuid
import concurrent.futures

import numpy as np
import sounddevice as sd

try:
    from faster_whisper import WhisperModel
    VOICE_AVAILABLE = True
except ImportError:
    VOICE_AVAILABLE = False

try:
    import pyttsx3
    TTS_AVAILABLE = True
except ImportError:
    TTS_AVAILABLE = False

import tiktoken

VOICE_AVAILABLE = False

# ── Multi-agent graph (replaces the single `agent`) ──────────────────────────
from agents.graph import get_app

# ── Supporting imports (unchanged) ───────────────────────────────────────────
from file_manager import AgentFileHandler, init_file_db
from watchdog.observers import Observer
from tools import WATCHED_FOLDER

from langchain_core.messages import AIMessage

CONTEXT_WINDOW = 131_072
_enc = tiktoken.get_encoding("cl100k_base")


def _count_tokens(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += len(_enc.encode(content))
        total += 4
    return total


SESSION_ID = str(uuid.uuid4())

console = Console()

# ── Rich rendering helpers (unchanged) ───────────────────────────────────────

def _render_dict_as_table(data: dict):
    table = Table(show_header=True, header_style="bold cyan", box=None)
    table.add_column("Setting", style="dim", width=20)
    table.add_column("Value", style="white")
    for key, value in data.items():
        if isinstance(value, list):
            value = ", ".join(
                str(v) if not isinstance(v, dict)
                else f"{v.get('name', '')} ({v.get('url', '')})"
                for v in value
            )
        table.add_row(str(key), str(value))
    console.print(table)


def _render_list(data: list):
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("", style="cyan")
    table.add_column("", style="white")
    for i, item in enumerate(data, 1):
        if isinstance(item, dict):
            name = item.get("name", str(item))
            url = item.get("url", "")
            table.add_row(f"[{i}]", f"{name} {f'→ {url}' if url else ''}")
        else:
            table.add_row(f"[{i}]", str(item))
    console.print(table)


def render_response(response_text: str):
    try:
        data = json.loads(response_text)
        if isinstance(data, dict):
            _render_dict_as_table(data)
            return
        if isinstance(data, list):
            _render_list(data)
            return
    except (json.JSONDecodeError, ValueError):
        pass

    has_markdown = (
        ("|" in response_text and "---" in response_text)
        or re.search(r"^\d+\.", response_text, re.MULTILINE)
        or "**" in response_text
        or re.search(r"^#{1,3} ", response_text, re.MULTILINE)
        or re.search(r"^- ", response_text, re.MULTILINE)
    )
    if has_markdown:
        console.print(Markdown(response_text))
        return

    console.print(Panel(response_text, border_style="blue", padding=(0, 1)))


# ── Wake-word voice config (unchanged) ───────────────────────────────────────

SAMPLE_RATE        = 16000
WAKE_PHRASE        = "hello"
WAKE_MODEL_SIZE    = "small"
COMMAND_MODEL_SIZE = "base"

WAKE_CHUNK_SEC        = 4.0
SILENCE_THRESHOLD     = 0.05
SILENCE_TIMEOUT       = 3.0
MAX_COMMAND_SEC       = 18
AUDIO_DEVICE          = 1

VAD_ENERGY_THRESHOLD  = 0.00
VOICE_DEBUG           = False

input_queue: queue.Queue[tuple] = queue.Queue()

_wake_model:    "WhisperModel | None" = None
_command_model: "WhisperModel | None" = None


def _update_rms(rms: float):
    if not VOICE_DEBUG:
        return
    colour = "bold green" if rms >= VAD_ENERGY_THRESHOLD else "dim red"
    console.print(f"[dim]🎙 RMS [/dim][{colour}]{rms:.4f}[/{colour}]")


def _load_wake_model() -> "WhisperModel":
    global _wake_model
    if _wake_model is None:
        console.print(f"[dim]Loading wake-word model ({WAKE_MODEL_SIZE})…[/dim]")
        _wake_model = WhisperModel(WAKE_MODEL_SIZE, device="cpu",
                                   compute_type="int8", num_workers=2, cpu_threads=4)
    return _wake_model


_model_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
_wake_model_future = _model_executor.submit(_load_wake_model)


def _load_command_model() -> "WhisperModel":
    global _command_model
    if _command_model is None:
        console.print(f"[dim]Loading command model ({COMMAND_MODEL_SIZE})…[/dim]")
        _command_model = WhisperModel(COMMAND_MODEL_SIZE, device="cpu",
                                      compute_type="int8", num_workers=2, cpu_threads=4)
    return _command_model


def _rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(audio ** 2)))


def _record_chunk(seconds: float) -> np.ndarray:
    frames = int(SAMPLE_RATE * seconds)
    recording = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1,
                       dtype="float32", blocking=True)
    return recording.flatten()


def _record_until_silence() -> np.ndarray:
    micro_sec    = 0.2
    micro_frames = int(SAMPLE_RATE * micro_sec)
    buffers:          list[np.ndarray] = []
    silent_duration:  float = 0.0
    total_sec:        float = 0.0
    has_speech:       bool  = False

    while total_sec < MAX_COMMAND_SEC:
        chunk = sd.rec(micro_frames, samplerate=SAMPLE_RATE,
                       channels=1, dtype="float32", blocking=True,
                       device=AUDIO_DEVICE).flatten()
        buffers.append(chunk)
        total_sec += micro_sec
        loud = _rms(chunk) >= SILENCE_THRESHOLD
        if loud:
            has_speech      = True
            silent_duration = 0.0
        elif has_speech:
            silent_duration += micro_sec
            if silent_duration >= SILENCE_TIMEOUT:
                break

    return np.concatenate(buffers) if buffers else np.array([], dtype="float32")


def _transcribe(model: "WhisperModel", audio: np.ndarray,
                initial_prompt: str = "") -> str:
    segments, info = model.transcribe(
        audio,
        language="en",
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        no_speech_threshold=0.6,
        initial_prompt=initial_prompt or None,
    )
    if info.duration == 0 or getattr(info, "no_speech_prob", 0) > 0.8:
        return ""
    return " ".join(s.text.strip() for s in segments).strip()


def _wake_detected(text: str) -> bool:
    cleaned   = re.sub(r"[^\w\s]", "", text.lower())
    words     = cleaned.split()
    wake_words = WAKE_PHRASE.lower().split()
    for i, word in enumerate(words):
        if _similar(word, wake_words[0]):
            if all(
                i + j < len(words) and _similar(words[i + j], wake_words[j])
                for j in range(1, len(wake_words))
            ):
                return True
    return False


def _similar(a: str, b: str) -> bool:
    if a == b:
        return True
    la, lb = len(a), len(b)
    if la == 0 or lb == 0:
        return False
    if abs(la - lb) / max(la, lb) > 0.5:
        return False
    dp = list(range(lb + 1))
    for i in range(1, la + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, lb + 1):
            temp = dp[j]
            if a[i - 1] == b[j - 1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j], dp[j - 1])
            prev = temp
    dist = dp[lb]
    similarity = 1 - dist / max(la, lb)
    return similarity >= 0.70


def _strip_wake(text: str) -> str:
    lower = text.lower()
    idx = lower.find(WAKE_PHRASE.lower())
    if idx != -1:
        return text[idx + len(WAKE_PHRASE):].strip(" ,.")
    cleaned_words = re.sub(r"[^\w\s]", "", lower).split()
    wake_words    = WAKE_PHRASE.lower().split()
    orig_words    = text.split()
    for i in range(len(cleaned_words) - len(wake_words) + 1):
        if all(_similar(cleaned_words[i + j], wake_words[j])
               for j in range(len(wake_words))):
            return " ".join(orig_words[i + len(wake_words):]).strip()
    return text


def _voice_thread():
    try:
        import time
        t0 = time.time()
        _wake_model_future.result()
        print(f"load wake model took: {time.time()-t0:.2f}s")
    except Exception as exc:
        console.print(f"[red]Could not load Whisper: {exc}[/red]")
        return

    console.print(
        f'[dim]🎤 Always-on voice ready — say [bold]"{WAKE_PHRASE}"[/bold] to give a command.[/dim]\n'
    )

    while True:
        try:
            chunk = _record_chunk(WAKE_CHUNK_SEC)
            rms = _rms(chunk)
            _update_rms(rms)
            if rms < VAD_ENERGY_THRESHOLD:
                continue

            wake_text = _transcribe(_load_wake_model(), chunk,
                                    initial_prompt=WAKE_PHRASE)
            if not wake_text or not _wake_detected(wake_text):
                continue

            console.print("[bold yellow]🎤 Listening…[/bold yellow]", end="\r")
            _load_command_model()

            inline = _strip_wake(wake_text)
            if inline:
                command_text = inline
            else:
                command_audio = _record_until_silence()
                if command_audio.size < SAMPLE_RATE * 0.3:
                    console.print(" " * 50, end="\r")
                    continue
                with console.status("[dim]Transcribing…[/dim]", spinner="dots"):
                    command_text = _transcribe(_load_command_model(),
                                               command_audio,
                                               initial_prompt="desktop control command")

            command_text = command_text.strip()
            if not command_text:
                console.print("[dim]Nothing heard after wake word.[/dim]")
                continue

            console.print(f"[bold magenta]🎤 You (voice):[/bold magenta] {command_text}")
            input_queue.put((command_text, True))

        except Exception as exc:
            console.print(f"[red]Voice error: {exc}[/red]")
            sd.sleep(500)


def _speak(text: str):
    if not TTS_AVAILABLE:
        return
    def _run():
        engine = pyttsx3.init()
        engine.setProperty("rate", 175)
        clean = re.sub(r"[*#`|>\[\]_]", "", text).strip()
        engine.say(clean)
        engine.runAndWait()
    threading.Thread(target=_run, daemon=True).start()


# ── Startup ───────────────────────────────────────────────────────────────────

console.print(Panel.fit(
    "[bold white]LLM Desktop Agent[/bold white]\n[dim]Local AI control for Windows[/dim]",
    border_style="blue",
    padding=(1, 4),
))

if VOICE_AVAILABLE:
    console.print(
        f'[dim]Type a command or say [bold]"{WAKE_PHRASE}"[/bold] to speak. '
        f"Type 'exit' or 'quit' to stop.[/dim]\n"
    )
    t = threading.Thread(target=_voice_thread, daemon=True)
    t.start()
else:
    console.print(
        "[dim]Type 'exit' or 'quit' to stop. "
        "(Voice unavailable — install sounddevice and faster-whisper)[/dim]\n"
    )

if TTS_AVAILABLE:
    console.print('[bold] TTS Enabled [/bold]')

memory.init_db()

init_file_db()
_file_observer = Observer()
_file_observer.schedule(AgentFileHandler(), WATCHED_FOLDER, recursive=True)
_file_observer.start()
console.print(f"[dim]📁 Watching workspace: {WATCHED_FOLDER}[/dim]\n")

memory.start_session(SESSION_ID)
conversation_history = memory.get_recent_context(session_id=SESSION_ID, n=10)

MAX_HISTORY = 20

# ── Build multi-agent graph (once at startup) ─────────────────────────────────
app = get_app()
console.print(
    "[dim]🧠 Multi-agent graph ready:[/dim]\n"
    "[dim]   supervisor → [bold]ppt_agent[/bold]    (PowerPoint creation)[/dim]\n"
    "[dim]              → [bold]window_agent[/bold] (audio · brightness · apps · layout · profiles)[/dim]\n"
    "[dim]              → [bold]shell_agent[/bold]  (system queries · commands · monitor)[/dim]\n"
    "[dim]              → [bold]file_agent[/bold]   (read · write · delete · move · list)[/dim]\n"
    "[dim]              → [bold]rag_agent[/bold]    (semantic file search · web search)[/dim]\n"
)


def _keyboard_thread():
    with patch_stdout(raw=True):
        while True:
            try:
                text = prompt("You: ").strip()
                input_queue.put((text, False))
            except (EOFError, KeyboardInterrupt):
                input_queue.put(("__EXIT__", False))
                break


keyboard_thread = threading.Thread(target=_keyboard_thread, daemon=True)
keyboard_thread.start()

# ── Main loop ─────────────────────────────────────────────────────────────────

while True:
    user_input, from_voice = input_queue.get()

    if user_input == "__EXIT__":
        console.print("\n[dim]Goodbye![/dim]")
        break

    if not user_input or not user_input.strip():
        continue

    if user_input.lower() in ("exit", "quit"):
        console.print("[dim]Goodbye![/dim]")
        break

    conversation_history.append({"role": "user", "content": user_input})
    memory.save_message(SESSION_ID, "user", user_input)

    similar = memory.search_similar(user_input, session_id=SESSION_ID, top_k=5)

    trimmed = conversation_history[-MAX_HISTORY:]

    used  = _count_tokens(trimmed)
    pct   = used / CONTEXT_WINDOW
    color = "green" if pct < 0.6 else "yellow" if pct < 0.85 else "red"
    console.print(
        f"[dim]Context: [{color}]{used:,} / {CONTEXT_WINDOW:,}[/{color}] "
        f"({pct*100:.0f}%) — {CONTEXT_WINDOW - used:,} tokens remaining[/dim]"
    )

    if similar:
        context_lines = "\n".join(
            f"[Past {m['role']}]: {m['content']}"
            for m in similar
            if m["score"] > 0.75
        )
        if context_lines:
            trimmed = [
                {"role": "system",
                 "content": f"RELEVANT PAST CONTEXT:\n{context_lines}"}
            ] + trimmed

    new_messages = []
    if context_lines:
        new_messages.append({"role": "system", "content": f"RELEVANT PAST CONTEXT:\n{context_lines}"})
    new_messages.append({"role": "user", "content": user_input})

    # ── Invoke multi-agent graph (replaces agent.invoke) ─────────────────────
    with console.status("[dim]thinking...[/dim]", spinner="dots"):
        result = app.invoke(
                                {"messages": new_messages},
                                config={"configurable": {"thread_id": SESSION_ID}},
                            )
    
    # Extract the last assistant message from the returned state
    result_messages = result.get("messages", [])
    assistant_message = next(
        (m for m in reversed(result_messages) if isinstance(m, AIMessage)),
        None,
    )

    if assistant_message:
        response_text = assistant_message.content.strip()
    else:
        response_text = "Action completed."

    conversation_history.append({"role": "assistant", "content": response_text})
    memory.save_message(SESSION_ID, "assistant", response_text)

    console.print("[bold green]Assistant:[/bold green]")
    render_response(response_text)
    if from_voice:
        _speak(response_text)
    console.print()