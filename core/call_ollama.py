from langchain.agents import create_agent
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


# ── Voice input (faster-whisper, always-on wake word) ────────────────────────
# numpy and sounddevice are top-level so Pylance knows they're always bound.
# faster_whisper is optional — if missing, voice is silently disabled but the
# agent continues to work normally via typed input.
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

CONTEXT_WINDOW = 131_072  # confirmed from model_info
_enc = tiktoken.get_encoding("cl100k_base")  # closest to Granite's gpt2/dbrx BPE

def _count_tokens(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += len(_enc.encode(content))
        total += 4  # per-message overhead (role, separators)
    return total


SESSION_ID = str(uuid.uuid4())  # unique ID for this run of the agent

from tools import (
    volume_control,
    mute_device,
    pause_media,
    set_active_window,
    get_current_volume,
    get_screen_brightness,
    adjust_screen_brightness,
    save_profile,
    del_profile,
    read_profile,
    open_application,
    get_installed_apps_tool,
    get_running_apps,
    query_system,
    run_system_command,
    show_system_monitor,
    kill_process,
    list_all_saved_profiles_names,
    resize_window,
    web_search,
    # ── file management ──────────────────────────────────────────────────────
    read_file,
    write_file,
    delete_file,
    move_file,
    list_files,
    search_files,
    search_file_content,
    get_workspace_tree,
    call_ppt_agent
)
from file_manager import (
    WATCHED_FOLDER,
    AgentFileHandler,
    init_file_db,
)
from watchdog.observers import Observer

agent = create_agent(
    model="ollama:granite4.1:8b",
    tools=[
           volume_control,
           mute_device,
           pause_media,
           set_active_window,
           get_current_volume,
           get_screen_brightness,
           adjust_screen_brightness,
           save_profile,
           del_profile,
           read_profile,
           open_application,
           get_installed_apps_tool,
           get_running_apps,
           query_system,
           run_system_command,
           show_system_monitor,
           kill_process,
           list_all_saved_profiles_names,
           resize_window,
           web_search,
           # ── file management ──────────────────────────────────────────────
           read_file,
           write_file,
           delete_file,
           move_file,
           list_files,
           search_files,
           search_file_content,
           get_workspace_tree,
           call_ppt_agent
           ],
    system_prompt="""You are a Windows computer control assistant.
                        IMPORTANT RULES:
                        - Only call a tool when the user explicitly asks you to perform an action
                        - If the user asks what tools you have, describe them from their descriptions — do NOT call them
                        - When applying a profile: first call read_profile to get the settings, 
                            then call volume_control, adjust_screen_brightness, and open_application 
                            separately using the values from the profile
                        - Before opening or focusing an app, call get_running_apps first to check 
                            if it is already open. If open use set_active_window, if not use open_application
                        - open_application accepts a list — pass all apps at once, not one at a time
                        - Listing tools = describe them in text only
                        - Dont be scared to call mutliptle tools your task is to be accurate not fast, 
                            take all the time you need to complete the task that means to call tools 
                            to make sure what the user is asking has been completed
                        - When the user says for eg: open notepad or open any application they might mean you need to run that application not just set it as active window
                        - query_system is for information gathering — network, processes, disk, software
                        - run_system_command is for actions — killing processes, installing apps, power commands
                        - Always use query_system before run_system_command when you need to verify something first
                        - For run_system_command, always confirm with the user before executing if it affects running processes or installs software
                        - To close an app: first call get_running_apps to confirm it is running, 
                            tell the user what you found and confirm you will close it, then call kill_process
                        - Never refuse to kill a user application citing admin privileges — 
                            only system processes require elevation
                        - Apps minimized to the system tray will appear in BACKGROUND/TRAY PROCESSES 
                            but not in VISIBLE WINDOWS — this is normal
                        - To close a tray app use kill_process, to focus a visible window use set_active_window
                        - If an app is not in either list, it is genuinely not running — say so clearly

                        FILE MANAGEMENT (workspace: agent_workspace on Desktop):
                        - All file paths are relative to the workspace root folder
                        - Use list_files to browse the workspace or any subfolder
                        - Use read_file to display a file's content to the user
                        - Use write_file to create a new file or overwrite an existing one
                        - Use move_file to rename or reorganise files and folders
                        - Use delete_file ONLY after the user has explicitly confirmed deletion — always ask first
                        - Use search_files to find files by name or extension (e.g. 'report', '.py')
                        - Use search_file_content for semantic/meaning-based search across file contents
                        - Never read, write, or delete files outside the workspace
                        - After writing a file, confirm the filename and location to the user

                        WINDOW RESIZING:
                        - Use resize_window when the user says 'move X to the left/right',
                            'snap X', 'make X take up 50%', 'put X in the corner', etc.
                        - Prefer named presets (left-half, right-half, top-left, etc.) over raw percentages
                        - If the user says '50% of the screen' without specifying which side, use left-half
                        - For 'side by side' requests on two apps: use left-half for the first and right-half for the second

                        PRESENTATION CREATION:
                        - Use call_ppt_agent when the user asks to make a PowerPoint, deck, or slides
                        - Pass a detailed task string: topic, slide count, theme, desired filename
                        - The PPT agent works autonomously — do not try to call design_slides or build_pptx yourself
                        - When it returns, tell the user the file path and offer to open it via open_application
                            
                        **CRITICAL**:
                        - Never invent or assume information not returned by a tool
                        - If a tool call fails or returns an error, report the exact error — do not explain why it might have failed
                        - Never describe actions you took unless a tool was actually called and returned a result
                        - If unsure, call the relevant tool to verify rather than reasoning from memory""",
)

console = Console()

# ── Rich rendering helpers ────────────────────────────────────────────────────

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


# ── Wake-word voice config ────────────────────────────────────────────────────
#
#   The microphone runs continuously.  A lightweight "tiny" Whisper model
#   checks each short chunk for the WAKE_PHRASE.  When heard, a heavier
#   "base" model transcribes the full command and sends it to the agent.
#
#   No keyboard package needed — the keyboard hotkey has been removed.
#   Requires:  pip install sounddevice numpy faster-whisper

SAMPLE_RATE        = 16000          # Whisper expects 16 kHz
WAKE_PHRASE        = "hello"        # ← change to whatever wake word you like
WAKE_MODEL_SIZE    = "small"        # "small" is still fast but far more accurate than "tiny"
COMMAND_MODEL_SIZE = "base"        # same model for commands — avoids loading two models

# Tuning
WAKE_CHUNK_SEC        = 4.0         # longer chunk = more phonetic context for Whisper (was 2.0)
SILENCE_THRESHOLD     = 0.05       # RMS below this = silence — raise if mic picks up PC fan noise
SILENCE_TIMEOUT       = 3.0         # seconds of silence to end a command
MAX_COMMAND_SEC       = 18         # hard cap on command length
AUDIO_DEVICE = 1

# Whisper VAD: skip transcription entirely when audio energy is very low.
# This prevents the model from hallucinating words on ambient noise/silence.
VAD_ENERGY_THRESHOLD  = 0.00       # chunks quieter than this are skipped without even running Whisper

# Set to True to print what Whisper hears on every chunk — helps diagnose wake-word issues.
# Turn off once working reliably.
VOICE_DEBUG = False

# Single queue that both voice and keyboard threads feed into.
input_queue: queue.Queue[tuple] = queue.Queue()

_wake_model:    "WhisperModel | None" = None
_command_model: "WhisperModel | None" = None

# ── Debug RMS output (non-intrusive via patch_stdout) ────────────────────────
# patch_stdout (started in _keyboard_thread) ensures any print() that happens
# while the user is typing is pushed *above* the prompt, never over it.

def _update_rms(rms: float):
    """Print RMS value above the input prompt without disturbing typing."""
    if not VOICE_DEBUG:
        return
    colour = "bold green" if rms >= VAD_ENERGY_THRESHOLD else "dim red"
    console.print(f"[dim]🎙 RMS [/dim][{colour}]{rms:.4f}[/{colour}]")


def _load_wake_model() -> "WhisperModel":
    global _wake_model
    if _wake_model is None:
        console.print(f"[dim]Loading wake-word model ({WAKE_MODEL_SIZE})…[/dim]")
        _wake_model = WhisperModel(WAKE_MODEL_SIZE, device="cpu", compute_type="int8", num_workers=2, cpu_threads=4)
    return _wake_model

_model_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
_wake_model_future = _model_executor.submit(_load_wake_model)

def _load_command_model() -> "WhisperModel":
    global _command_model
    if _command_model is None:
        console.print(f"[dim]Loading command model ({COMMAND_MODEL_SIZE})…[/dim]")
        _command_model = WhisperModel(COMMAND_MODEL_SIZE, device="cpu", compute_type="int8", num_workers=2, cpu_threads=4)
    return _command_model


def _rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(audio ** 2)))


def _record_chunk(seconds: float) -> np.ndarray:
    """Synchronously record `seconds` of audio and return a float32 array."""
    frames = int(SAMPLE_RATE * seconds)
    recording = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1,
                       dtype="float32", blocking=True)
    return recording.flatten()


def _record_until_silence() -> np.ndarray:
    """
    Records short bursts until SILENCE_TIMEOUT seconds of consecutive
    silence or MAX_COMMAND_SEC total.  Returns the full concatenated audio.

    Uses 0.2 s micro-chunks so silence detection is responsive — the old
    5-second chunks caused the agent to wait 5 full seconds before stopping,
    which clipped commands and felt broken.
    """
    micro_sec    = 0.2                         # poll every 200 ms
    micro_frames = int(SAMPLE_RATE * micro_sec)

    buffers:          list[np.ndarray] = []
    silent_duration:  float = 0.0
    total_sec:        float = 0.0
    has_speech:       bool  = False            # don't stop on leading silence

    while total_sec < MAX_COMMAND_SEC:
        chunk = sd.rec(micro_frames, samplerate=SAMPLE_RATE,
                       channels=1, dtype="float32", blocking=True, device=AUDIO_DEVICE).flatten()
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
        # else: still in leading silence — keep waiting for speech to start

    return np.concatenate(buffers) if buffers else np.array([], dtype="float32")


def _transcribe(model: "WhisperModel", audio: np.ndarray, initial_prompt: str = "") -> str:
    """
    beam_size=5 searches 5 candidates instead of 1 — much more accurate,
    still fast enough on CPU for short clips.
    vad_filter skips silent regions so Whisper does not hallucinate on noise.
    no_speech_threshold returns empty string when the clip is probably not speech.
    initial_prompt seeds the decoder so it expects phrases like the wake word.
    """
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
    """
    Fuzzy wake-word check.  Handles Whisper quirks like:
      - "hey, agent" / "Hey Agent" / "hey Agent!"
      - "hey aged" / "hey aiden"  (common mishearings)
      - The wake phrase split across punctuation
    Strategy: strip punctuation, check for all wake-phrase words in order.
    """
    import re
    cleaned = re.sub(r"[^\w\s]", "", text.lower())   # remove punctuation
    words   = cleaned.split()

    wake_words = WAKE_PHRASE.lower().split()          # e.g. ["hey", "agent"]
    # Look for the first wake word, then check each following word fuzzy-matches
    for i, word in enumerate(words):
        if _similar(word, wake_words[0]):
            # Check remaining wake words follow consecutively
            if all(
                i + j < len(words) and _similar(words[i + j], wake_words[j])
                for j in range(1, len(wake_words))
            ):
                return True
    return False


def _similar(a: str, b: str) -> bool:
    """
    True if two words are phonetically/typographically close enough.
    Uses normalised Levenshtein edit distance — handles transpositions,
    insertions, and deletions that the old character-overlap ratio missed.
    e.g. "helo" vs "hello" = 1 edit in 5 chars = 0.8 similarity → True
         "hay"  vs "hello" = 3 edits in 5 chars = 0.4 similarity → False
    """
    if a == b:
        return True
    # Levenshtein distance via DP (no extra deps needed)
    la, lb = len(a), len(b)
    if la == 0 or lb == 0:
        return False
    # Quick length-difference shortcut
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
    """Remove wake phrase prefix from transcript if present."""
    import re
    # Try exact first
    lower = text.lower()
    idx = lower.find(WAKE_PHRASE.lower())
    if idx != -1:
        return text[idx + len(WAKE_PHRASE):].strip(" ,.")
    # Fuzzy: drop everything up to and including the last wake-phrase word
    cleaned_words = re.sub(r"[^\w\s]", "", lower).split()
    wake_words    = WAKE_PHRASE.lower().split()
    orig_words    = text.split()
    for i in range(len(cleaned_words) - len(wake_words) + 1):
        if all(_similar(cleaned_words[i + j], wake_words[j]) for j in range(len(wake_words))):
            return " ".join(orig_words[i + len(wake_words):]).strip()
    return text


def _voice_thread():
    """
    Always-on wake-word listener.

    1. Record a short chunk (WAKE_CHUNK_SEC).
    2. Transcribe with the tiny model — skip if silent.
    3. Wake phrase found → record full command → transcribe with base model
       → push transcript to input_queue.
    """
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
            # ── listen for wake phrase ────────────────────────────────────────
            chunk = _record_chunk(WAKE_CHUNK_SEC)

            # Skip very quiet chunks — Whisper hallucinates text on silence/noise.
            # VAD_ENERGY_THRESHOLD is deliberately lower than SILENCE_THRESHOLD so
            # we only gate out near-total silence, not quiet speech.
            rms = _rms(chunk)
            _update_rms(rms)
            if rms < VAD_ENERGY_THRESHOLD:
                continue

            wake_text = _transcribe(_load_wake_model(), chunk, initial_prompt=WAKE_PHRASE)
            if not wake_text or not _wake_detected(wake_text):
                continue

            # ── wake phrase heard — capture command ───────────────────────────
            console.print("[bold yellow]🎤 Listening…[/bold yellow]", end="\r")
            _load_command_model()   # pre-load while user formulates command

            inline = _strip_wake(wake_text)
            if inline:
                # Full command was in the same chunk as the wake phrase
                command_text = inline
            else:
                # Command follows in the next audio burst
                command_audio = _record_until_silence()
                if command_audio.size < SAMPLE_RATE * 0.3:
                    console.print(" " * 50, end="\r")
                    continue
                with console.status("[dim]Transcribing…[/dim]", spinner="dots"):
                    command_text = _transcribe(_load_command_model(), command_audio, initial_prompt="desktop control command")

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
    """Speak response text in a background thread so it doesn't block the main loop."""
    if not TTS_AVAILABLE:
        return
    def _run():
        engine = pyttsx3.init()
        engine.setProperty("rate", 175)   # adjust speed to taste
        # Strip markdown/symbols so they don't get read aloud literally
        clean = re.sub(r"[*#`|>\[\]_]", "", text).strip()
        engine.say(clean)
        engine.runAndWait()
    threading.Thread(target=_run, daemon=True).start()


# ── Main loop ─────────────────────────────────────────────────────────────────

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

# ── File system watcher — keeps the index in sync automatically ───────────────
import time
t0 = time.time()
init_file_db()
print(f"init_file_db took {time.time()-t0:.2f}s")
_file_observer = Observer()
_file_observer.schedule(AgentFileHandler(), WATCHED_FOLDER, recursive=True)
_file_observer.start()
console.print(f"[dim]📁 Watching workspace: {WATCHED_FOLDER}[/dim]\n")

# seed conversation history with recent past context
memory.start_session(SESSION_ID)
conversation_history = memory.get_recent_context(session_id=SESSION_ID, n=10)


MAX_HISTORY = 20

def _keyboard_thread():
    """
    Reads typed input using prompt_toolkit wrapped in patch_stdout.

    patch_stdout intercepts all writes to stdout/stderr that happen while
    the prompt is active and redraws them *above* the input line — so RMS
    debug prints, voice status messages, and agent output never overlap
    whatever the user is currently typing.
    """
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

while True:
    # Both voice and keyboard feed input_queue — no blocking race condition.
    user_input, from_voice = input_queue.get()

    if user_input == "__EXIT__":
        console.print("\n[dim]Goodbye![/dim]")
        break

    if not user_input:
        continue

    if user_input.lower() in ("exit", "quit"):
        console.print("[dim]Goodbye![/dim]")
        break

    conversation_history.append({"role": "user", "content": user_input})

    # save user message immediately
    memory.save_message(SESSION_ID, "user", user_input)

    # retrieve semantically similar past exchanges
    similar = memory.search_similar(user_input, session_id=SESSION_ID, top_k=5)

    trimmed = conversation_history[-MAX_HISTORY:]

    used = _count_tokens(trimmed)
    pct  = used / CONTEXT_WINDOW
    color = "green" if pct < 0.6 else "yellow" if pct < 0.85 else "red"
    console.print(
        f"[dim]Context: [{color}]{used:,} / {CONTEXT_WINDOW:,}[/{color}] "
        f"({pct*100:.0f}%) — {CONTEXT_WINDOW - used:,} tokens remaining[/dim]"
    )


    if similar:
        context_lines = "\n".join(
            f"[Past {m['role']}]: {m['content']}"
            for m in similar
            if m["score"] > 0.75  # only inject if reasonably relevant
        )
        if context_lines:
            trimmed = [
                {"role": "system", "content": f"RELEVANT PAST CONTEXT:\n{context_lines}"}
            ] + trimmed

    with console.status("[dim]thinking...[/dim]", spinner="dots"):
        result = agent.invoke({"messages": trimmed})

    assistant_message = result["messages"][-1]
    blocks = getattr(assistant_message, "content_blocks", None)

    if blocks and len(blocks) > 0 and "text" in blocks[0]:
        response_text = blocks[0]["text"]
    elif hasattr(assistant_message, "content") and assistant_message.content:
        if isinstance(assistant_message.content, str):
            response_text = assistant_message.content
        elif isinstance(assistant_message.content, list):
            response_text = " ".join(
                block.get("text", "") for block in assistant_message.content
                if isinstance(block, dict) and "text" in block
            )
        else:
            response_text = str(assistant_message.content)
    else:
        response_text = "Action completed."

    conversation_history.append({"role": "assistant", "content": response_text})
    memory.save_message(SESSION_ID, "assistant", response_text)

    console.print("[bold green]Assistant:[/bold green]")
    render_response(response_text)
    if from_voice:
        _speak(response_text)
    console.print()