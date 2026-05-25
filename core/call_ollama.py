from langchain.agents import create_agent
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markdown import Markdown
from rich.text import Text
import json
import re
import threading
import queue

# ── Voice input (faster-whisper) ─────────────────────────────────────────────
# numpy and sounddevice are top-level so Pylance knows they're always bound.
# Only faster_whisper and keyboard are truly optional — if missing, voice is
# silently disabled but the agent continues to work normally.
import numpy as np
import sounddevice as sd

try:
    from faster_whisper import WhisperModel
    VOICE_AVAILABLE = True
except ImportError:
    VOICE_AVAILABLE = False

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
)

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

                        WINDOW RESIZING:
                        - Use resize_window when the user says 'move X to the left/right',
                            'snap X', 'make X take up 50%', 'put X in the corner', etc.
                        - Prefer named presets (left-half, right-half, top-left, etc.) over raw percentages
                        - If the user says '50% of the screen' without specifying which side, use left-half
                        - For 'side by side' requests on two apps: use left-half for the first and right-half for the second
                            
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


# ── Voice input ───────────────────────────────────────────────────────────────
#
#   Press and HOLD V (or the configured hotkey) to record.
#   Release to transcribe and inject as typed input.
#   Requires:  pip install sounddevice numpy faster-whisper keyboard
#
# We use a shared queue so the voice thread can hand transcripts
# back to the main input loop without blocking it.

VOICE_HOTKEY = "shift + v"          # change to e.g. "space" if you prefer
SAMPLE_RATE  = 16000        # Whisper expects 16 kHz
WHISPER_MODEL_SIZE = "base" # tiny / base / small — base is the best CPU/accuracy balance

# Single queue that both voice and keyboard threads feed into.
# The main loop just blocks on input_queue.get() — no more racing.
input_queue: queue.Queue[str] = queue.Queue()
_whisper_model = None        # loaded lazily on first use


def _load_whisper():
    global _whisper_model
    if _whisper_model is None:
        console.print("[dim]Loading Whisper model (first-time only)…[/dim]")
        _whisper_model = WhisperModel(
            WHISPER_MODEL_SIZE,
            device="cpu",
            compute_type="int8",   # fastest on CPU, negligible accuracy loss
        )
    return _whisper_model


def _record_while_held(hotkey: str) -> np.ndarray:
    """
    Records audio into a buffer for as long as the hotkey is held.
    Returns a float32 numpy array at SAMPLE_RATE.
    Requires the `keyboard` package.
    """
    import keyboard  # imported here so missing package only breaks voice, not the agent
    frames = []

    def _callback(indata, frame_count, time_info, status):
        frames.append(indata.copy())

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                        dtype="float32", callback=_callback):
        while keyboard.is_pressed(hotkey):
            sd.sleep(50)  # poll every 50 ms

    if not frames:
        return np.array([], dtype="float32")
    return np.concatenate(frames, axis=0).flatten()


def _voice_thread():
    """
    Runs in a background daemon thread.
    Waits for the hotkey press, records, transcribes, and pushes the
    transcript string into voice_queue for the main loop to consume.
    """
    try:
        import keyboard
    except ImportError:
        console.print(
            "[yellow]Voice input disabled — install the 'keyboard' package to enable it.[/yellow]"
        )
        return

    console.print(
        f"[dim]🎤 Voice ready — hold [bold]{VOICE_HOTKEY.upper()}[/bold] to speak.[/dim]\n"
    )

    while True:
        # block until the hotkey is pressed (edge trigger)
        keyboard.wait(VOICE_HOTKEY)

        console.print("[bold yellow]🎤 Recording…[/bold yellow]", end="\r")
        audio = _record_while_held(VOICE_HOTKEY)

        if audio.size < SAMPLE_RATE * 0.3:
            # less than 0.3 s — probably accidental press
            console.print(" " * 30, end="\r")
            continue

        with console.status("[dim]Transcribing…[/dim]", spinner="dots"):
            try:
                model = _load_whisper()
                segments, _ = model.transcribe(audio, language="en", beam_size=1)
                transcript = " ".join(s.text.strip() for s in segments).strip()
            except Exception as exc:
                console.print(f"[red]Transcription error: {exc}[/red]")
                continue

        if transcript:
            console.print(f"[bold magenta]🎤 You (voice):[/bold magenta] {transcript}")
            input_queue.put(transcript)
        else:
            console.print("[dim]Nothing heard.[/dim]")


# ── Main loop ─────────────────────────────────────────────────────────────────

console.print(Panel.fit(
    "[bold white]LLM Desktop Agent[/bold white]\n[dim]Local AI control for Windows[/dim]",
    border_style="blue",
    padding=(1, 4),
))

if VOICE_AVAILABLE:
    console.print(
        f"[dim]Type your command or hold [bold]{VOICE_HOTKEY.upper()}[/bold] to speak. "
        f"Type 'exit' or 'quit' to stop.[/dim]\n"
    )
    t = threading.Thread(target=_voice_thread, daemon=True)
    t.start()
else:
    console.print(
        "[dim]Type 'exit' or 'quit' to stop. "
        "(Voice unavailable — install sounddevice, faster-whisper, keyboard)[/dim]\n"
    )

conversation_history = []

def _keyboard_thread():
    """Reads typed input in a background thread and feeds it into input_queue."""
    while True:
        try:
            text = console.input("[bold blue]You:[/bold blue] ").strip()
            input_queue.put(text)
        except (EOFError, KeyboardInterrupt):
            input_queue.put("__EXIT__")
            break

keyboard_thread = threading.Thread(target=_keyboard_thread, daemon=True)
keyboard_thread.start()

while True:
    # Both voice and keyboard feed input_queue — no blocking race condition.
    user_input = input_queue.get()

    if user_input == "__EXIT__":
        console.print("\n[dim]Goodbye![/dim]")
        break

    if not user_input:
        continue

    if user_input.lower() in ("exit", "quit"):
        console.print("[dim]Goodbye![/dim]")
        break

    conversation_history.append({"role": "user", "content": user_input})

    with console.status("[dim]thinking...[/dim]", spinner="dots"):
        result = agent.invoke({"messages": conversation_history})

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

    console.print("[bold green]Assistant:[/bold green]")
    render_response(response_text)
    console.print()