# 🖥️ llm-desktop-agent

> Control your Windows PC using natural language — powered entirely by local LLMs. No cloud. No API keys. No subscriptions.

![Python](https://img.shields.io/badge/Python-3.12.4+-3776AB?style=flat&logo=python&logoColor=white)
![LangChain](https://img.shields.io/badge/LangChain-1.0+-1C3C3C?style=flat)
![Ollama](https://img.shields.io/badge/Ollama-local-black?style=flat)
![Windows](https://img.shields.io/badge/Windows-10%2F11-0078D4?style=flat&logo=windows&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=flat)
![Open Source](https://img.shields.io/badge/Open%20Source-%E2%9D%A4-red?style=flat)

---

![llm-desktop-agent terminal demo](assets/preview.png)

---

## What is this?

`llm-desktop-agent` is a fully local AI agent that lets you control your Windows machine through natural language. Ask it to adjust your volume, change screen brightness, open apps, manage profiles, close processes, query system info, read and write files in your workspace, or chain multiple actions together — all without touching the cloud.

It uses [Ollama](https://ollama.com) to run LLMs locally on your hardware and [LangChain](https://langchain.com) to give the model real tools it can act on. The terminal interface is built with [Rich](https://github.com/Textualize/rich) for clean formatted output, and a live system monitor is available via [Textual](https://github.com/Textualize/textual).

```
You: apply my gaming profile
Assistant: Volume → 100%, Brightness → 80. Opened Valorant and Chrome (YouTube). Windows arranged side by side.

You: what is eating my RAM right now?
Assistant: Top memory consumers: chrome.exe (892 MB), ollama.exe (4.1 GB), discord.exe (412 MB)...

You: close whatsapp its not needed
Assistant: Found WhatsApp running in background. Closing it now... Done.

You: show me the workspace structure
Assistant: 📁 agent_workspace/
├── 📁 projects/
│   ├── 📄 main.py  (2.1 KB)
│   └── 📄 utils.py  (890 B)
└── 📄 notes.txt  (144 B)

You: create a file called ideas.txt with my three ideas
Assistant: Written and indexed: 'ideas.txt'

You: find all files about the project deadline
Assistant: [0.91] notes.txt — "deadline is end of month, need to finish the API layer..."
```

---

## Features

### System Control
- **Volume** — get and set system volume, mute/unmute with auto-unmute on volume change (setting volume while muted automatically unmutes)
- **Screen brightness** — read and adjust display brightness
- **Media control** — play/pause media globally across any app via virtual media keys
- **App launcher** — open any installed application by name with fuzzy matching; sources Start Menu shortcuts, registry, and Windows built-ins (notepad, calc, mspaint, etc.), supports URLs for browser apps
- **Multi-app launch** — open multiple apps at once with automatic window arrangement: 1-app fullscreen, 2-app side-by-side, 3-app main+stack, 4-app grid
- **Window management** — bring any running app to the foreground; distinguishes visible windows from tray/background processes
- **Window resize & reposition** — move and resize any window by name using named presets or exact percentages (see below)
- **Process management** — close any running application including system tray apps, with fuzzy process name matching; uses `taskkill /T` to terminate full process trees including Store/AppContainer apps

### Window Layout Presets

Say things like _"move chrome to the left half"_, _"snap notepad to the top right"_, _"put spotify in the center third"_ — the agent maps natural language to one of 15 named presets:

| Preset | Description |
|---|---|
| `left-half` / `right-half` | Fill left or right 50% |
| `top-half` / `bottom-half` | Fill top or bottom 50% |
| `top-left` / `top-right` / `bottom-left` / `bottom-right` | Quarter-screen corners |
| `left-third` / `center-third` / `right-third` | Horizontal thirds |
| `left-two-thirds` / `right-two-thirds` | Two-thirds layouts |
| `maximized` | Full screen |
| `centered` | Floating centered (50% wide, 80% tall) |

Custom pixel-exact placement is also supported via x/y/width/height percentages.

### File Management

The agent has full read/write access to a sandboxed workspace folder (`agent_workspace/` on your Desktop). All file operations are path-traversal safe — the agent cannot access anything outside this folder.

- **`read_file`** — read the content of any supported file (txt, md, py, js, json, csv, pdf, docx, xlsx, pptx)
- **`write_file`** — create or overwrite a file; parent folders are created automatically; file is re-indexed for semantic search immediately after writing
- **`delete_file`** — permanently delete a file or empty folder; always asks for confirmation first
- **`move_file`** — move or rename files and folders within the workspace
- **`list_files`** — list the contents of the workspace root or any subfolder with file sizes
- **`get_workspace_tree`** — show the full recursive folder tree with `├──` connectors and file sizes; the agent calls this automatically before complex file operations to understand the layout
- **`search_files`** — find files by name or extension (e.g. "find all .py files", "where is report")
- **`search_file_content`** — semantic search across all indexed file content using vector embeddings; finds files by meaning, not just keyword

Files are indexed into SQLite using [nomic-embed-text](https://ollama.com/library/nomic-embed-text) embeddings and watched automatically via [watchdog](https://github.com/gorakhargosh/watchdog) — any file change on disk (created, modified, deleted) updates the index in real time without restarting the agent.

Supported file types for reading and indexing:

| Extension | Reader |
|---|---|
| `.txt`, `.md`, `.py`, `.js`, `.json` | Plain text |
| `.csv` | `csv` module |
| `.pdf` | PyMuPDF (`fitz`) — text-based only, scanned PDFs are rejected |
| `.docx` | `python-docx` |
| `.xlsx` | openpyxl (via `read_file_content`) |
| `.pptx` | `python-pptx` |

Code files (`.py`, `.js`, `.ts`, `.go`, `.rs`, `.java`, `.cs`, and more) are semantically chunked at the function/method level using [tree-sitter](https://tree-sitter.github.io/tree-sitter/) when the grammar package is installed, falling back to overlapping line chunks otherwise.

### Intelligence
- **Profile system** — save and load named configurations with multiple apps, optional URLs per app, volume and brightness (e.g. "study", "gaming", "focus")
- **Multi-step reasoning** — one request chains multiple tools automatically
- **System awareness** — checks if apps are already running before opening, detects tray vs visible windows, verifies actions with follow-up tool calls
- **CMD access** — controlled read/write access to Windows command line split into two tools: read-only queries (`query_system`) and state-changing commands (`run_system_command`) with mandatory user confirmation
- **Command safety layer** — blocklist of destructive operations (`del`, `format`, `reg delete`, `diskpart`, symlink creation, script file writes, etc.) refused regardless of how they're requested; separate confirmation gate for shutdown, restart, `winget install/uninstall`, and network changes
- **Persistent memory** — SQLite-backed conversation history with semantic retrieval; past relevant exchanges are injected into context automatically using cosine similarity scoring

### Monitoring
- **Live system dashboard** — real-time terminal UI showing CPU, RAM, GPU, GPU VRAM, disk read/write MB/s, battery status and time remaining
- **Process table** — top 30 processes sorted by CPU with colour-coded usage (red >50%, yellow >20%)
- **Sparkline history** — rolling 40-sample graphs for each metric updating every 2 seconds
- **System info queries** — ask in natural language about network, disk, installed software, running processes

### Interface
- **Voice input** — always-on voice activation using a local wake word (`"hello"`); after activation, queries are transcribed entirely on-device via faster-whisper using two CPU-optimized int8 models:
  - `tiny` model for lightweight wake-word detection
  - `base` model for accurate query transcription
- No audio ever leaves the machine
- Whisper models load lazily on first use and are cached afterwards for fast startup
- **Voice feedback (TTS)** — voice commands are answered aloud via pyttsx3; the agent speaks its response back to you entirely on-device. Keyboard input stays silent — only voice-triggered commands get spoken responses, keeping the two modes cleanly separated
- No audio ever leaves the machine
- **Unified input queue** — voice and keyboard both feed the same queue so transcripts are dispatched immediately without pressing Enter
- **Rich terminal output** — markdown rendering, tables, panels, syntax highlighting
- **Thinking spinner** — visual feedback while agent is processing
- **Smart rendering** — auto-detects JSON, markdown tables, bullet lists and renders each appropriately
- **Graceful degradation** — voice dependencies (faster-whisper, sounddevice, keyboard) are optional; agent runs normally in text-only mode if they are not installed
- **100% local** — nothing leaves your machine

---

## Tech Stack

| Tool | Role |
|---|---|
| [Ollama](https://ollama.com) | Local LLM inference runtime |
| [LangChain](https://python.langchain.com) | Agent framework and tool orchestration |
| [Rich](https://github.com/Textualize/rich) | Terminal formatting, markdown, tables |
| [Textual](https://github.com/Textualize/textual) | Interactive live system monitor TUI |
| [psutil](https://github.com/giampaolo/psutil) | Cross-platform process and system utilities |
| [GPUtil](https://github.com/anderskm/gputil) | NVIDIA GPU usage and VRAM monitoring |
| [pycaw](https://github.com/AndreMiras/pycaw) | Windows audio control via COM API |
| [screen-brightness-control](https://github.com/Crozzers/screen-brightness-control) | Display brightness management |
| [pyautogui](https://pyautogui.readthedocs.io) | Global media key simulation |
| [pygetwindow](https://github.com/asweigart/PyGetWindow) | Window focus and management |
| [pywin32](https://github.com/mhammond/pywin32) | Windows API access, `.lnk` shortcut resolution, low-level window positioning |
| [comtypes](https://github.com/enthought/comtypes) | COM interface bindings for audio |
| [langchain-ollama](https://python.langchain.com/docs/integrations/llms/ollama) | Ollama embeddings via LangChain |
| [watchdog](https://github.com/gorakhargosh/watchdog) | File system event monitoring for live index updates |
| [tree-sitter](https://tree-sitter.github.io/tree-sitter/) | AST-based semantic chunking of code files (optional) |
| [PyMuPDF](https://pymupdf.readthedocs.io) | PDF text extraction |
| [python-docx](https://python-docx.readthedocs.io) | Word document reading |
| [python-pptx](https://python-pptx.readthedocs.io) | PowerPoint reading |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | Local speech-to-text transcription (optional) |
| [sounddevice](https://python-sounddevice.readthedocs.io) | Microphone audio capture (optional) |
| [keyboard](https://github.com/boppreh/keyboard) | Global hotkey detection for push-to-talk (optional) |
| [pyttsx3](https://github.com/nateshmbhat/pyttsx3) | Offline text-to-speech for voice responses (optional) |

Every single dependency is free and open source.

---

## Requirements

- Windows 10 or 11
- Python 3.12.4+
- [Ollama](https://ollama.com/download) installed and running
- A GPU is recommended (tested on RTX 4060 8GB with NVIDIA) but not required
- For GPU monitoring: NVIDIA GPU with drivers installed (AMD GPU monitoring not currently supported)

### Recommended Models

| Model | VRAM | Tool Calling | Notes |
|---|---|---|---|
| `qwen2.5:3b` | ~2.5GB | Decent | Lightweight, fast |
| `granite4.1:8b` | ~5GB | Good | Tested model for this project |
| `qwen2.5:7b` | ~5GB | Very Good | Best balance |
| `mistral:7b` | ~4.5GB | Good | Strong reasoning |

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/DeepeshAlwani/llm-desktop-agent.git
cd llm-desktop-agent

# 2. Create and activate a virtual environment
python -m venv agent_env
agent_env\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. (Optional) Install voice input dependencies
pip install faster-whisper sounddevice keyboard numpy

# 5. Pull models via Ollama
ollama pull granite4.1:8b
ollama pull nomic-embed-text-v2-moe

# 6. Create the workspace folder
mkdir "%USERPROFILE%\Desktop\agent_workspace"

# 7. Run the agent
cd core
python call_ollama.py
```

> **Note:** Run from Windows Terminal (not VS Code's integrated terminal) for best results. The live system monitor spawns a new terminal window and requires full TTY support.

---

## Voice Input

The assistant uses always-on voice activation — just say **"hello"** to wake it up and start speaking your query.

- Runs entirely on CPU using faster-whisper with **two optimized models**:
  - `tiny` model (int8) for lightweight wake-word detection (`"hello"`)
  - `base` model (int8) for accurate transcription of the actual query
- The Whisper models download and load automatically on first use (~200MB combined, cached afterwards)
- No hotkeys required — voice activation is fully hands-free
- You can change the wake word by editing `WAKE_PHRASE` in `call_ollama.py`
- You can change the transcription model size (`tiny` / `base` / `small`) by editing `WAKE_MODEL_SIZE` / `COMMAND_MODEL_SIZE`
- Voice support remains optional — if the required packages are not installed, the agent automatically falls back to text-only mode without errors

---

## Project Structure

```
llm-desktop-agent/
├── core/
│   ├── call_ollama.py      # Agent loop, conversation management, Rich rendering, voice input
│   ├── tools.py            # All LangChain tools (27 tools)
│   ├── file_manager.py     # File reading, indexing, tree-sitter chunking, watchdog handler
│   ├── memory.py           # SQLite conversation history and semantic memory retrieval
│   └── dashboard.py        # Textual live system monitor (launched separately)
├── profiles/               # Saved user profiles — auto-created on first save
├── agent_workspace/        # Sandboxed folder the agent can read/write (on Desktop)
├── agent_files.db          # SQLite index for workspace files and embeddings
├── agent_memory.db         # SQLite store for conversation history and embeddings
├── assets/
│   └── preview.png         # Terminal screenshot for README
├── requirements.txt
└── README.md
```

The agent logic, tool definitions, file management, and memory are kept intentionally separate so components can be swapped or extended without touching the others.

---

## How It Works

```
User input (typed or voice)
        ↓
   Unified input queue
   (keyboard thread + voice thread)
        ↓
   Semantic memory retrieval
   (past relevant exchanges injected into context)
        ↓
   LangChain Agent
   + 27 tool definitions
        ↓
   LLM decides which tool(s) to call and in what order
        ↓
   Python dispatcher executes:
   pycaw / pyautogui / subprocess / sbc / psutil / win32api / win32gui
   file_manager / sqlite / watchdog / tree-sitter / ollama embeddings
        ↓
   Tool result returned to LLM
        ↓
   Rich-formatted response to user
```

The LLM never directly touches your system or files. It outputs structured tool calls, and Python executes them. Every action is inspectable, restrictable, and extensible.

---

## Profiles

Profiles let you save named configurations and apply them with a single command. Each profile supports multiple apps, optional URLs per app, volume and brightness.

```
You: save a profile — chrome with youtube, notepad, volume 20, brightness 40, call it study
You: apply my study profile
You: what profiles do I have?
You: delete the gaming profile
```

Profile JSON structure:

```json
{
    "apps": [
        {"name": "chrome", "url": "https://youtube.com"},
        {"name": "notepad", "url": null}
    ],
    "screen_brightness": 40,
    "volume_level": 20
}
```

Profiles are stored as plain JSON in `profiles/` — human-readable and editable by hand.

---

## File Workspace

The agent's file tools are sandboxed to `agent_workspace/` on your Desktop. Drop any files in there and the watchdog observer will index them automatically on the next change event.

```
You: show me the workspace structure
You: read notes.txt
You: create a file called todo.md with my task list
You: find all python files
You: search my files for anything about the API design
You: rename ideas.txt to brainstorm.txt
You: delete draft.txt    ← agent will ask you to confirm first
```

The file index (`agent_files.db`) persists between sessions — files indexed in a previous run are still searchable next time.

---

## CMD Access

The agent has controlled access to the Windows command line split into two tools:

- **`query_system`** — read-only queries: `ipconfig`, `tasklist`, `netstat`, `systeminfo`, `winget list`, `ping` etc.
- **`run_system_command`** — state-changing actions: `taskkill`, `winget install`, `netsh`, power commands — always with user confirmation

Destructive commands (`del`, `format`, `reg delete`, `diskpart`, symlink creation, script file writes, etc.) are blocked regardless of how they are requested.

---

## System Monitor

Launch the live dashboard by asking the agent or running directly:

```bash
python dashboard.py
```

| Metric | Details |
|---|---|
| CPU % | Overall usage with sparkline history |
| RAM % | Memory usage with sparkline history |
| GPU % | NVIDIA GPU load (requires GPUtil) |
| GPU RAM % | VRAM usage percentage |
| Disk Read/Write | Real MB/s delta (not cumulative), updated every 2s |
| Battery | Percentage, plug status, time remaining |
| Process table | Top 30 by CPU — colour coded red >50% / yellow >20% |

Keyboard shortcuts: `q` quit, `r` refresh processes, `d` toggle dark/light theme.

---

## Roadmap

### Near Term
- [✔️] Memory between sessions (SQLite-backed conversation history)
- [✔️] File management — read, write, delete, move, search, semantic content search
- [✔️] Wake word detection so voice activates hands-free (no hotkey hold)
- [ ] Night light toggle via Windows registry
- [ ] Resolution switching
- [ ] System shutdown / restart / sleep commands
- [ ] WhatsApp and other UWP app process name alias map
- [✔️] Voice feedback — TTS responses so the agent speaks back
- [ ] Per-app volume control (set Spotify to 40% without touching system volume)

### Medium Term
- [ ] Scheduled actions ("mute at 11pm every night") via APScheduler
- [ ] Snapshot and restore system state before profile apply
- [ ] Multi-monitor support — specify which display to move a window to
- [ ] Multi-monitor brightness — set brightness per display independently
- [ ] AMD GPU monitoring support
- [ ] Clipboard read/write — "copy this text to my clipboard" or "what's in my clipboard"

### Long Term
- [ ] Native Windows GUI using PySide6 (no Electron, no web wrapper)
- [ ] LangGraph-based agent for more complex multi-step planning
- [ ] Plugin system so users can add tools without modifying core files
- [ ] Auto-discovery of user preferences over time

---

## Contributing

Contributions are very welcome.

### Getting Started

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature-name`
3. Make your changes
4. Test on a Windows machine — this project is Windows-only by design
5. Open a pull request with a clear description of what you added and why

### What to Contribute

- **New tools** — anything controllable via Python on Windows. Add in `tools.py` with the `@tool` decorator and a clear docstring.
- **Bug fixes** — especially around app launching, process detection, or COM audio edge cases
- **Model testing** — tested a model not in the recommended list? Open a PR updating the table
- **App aliases** — know the real process name for a common app? Add it to `APP_ALIASES` in `tools.py`
- **Documentation** — if something is unclear, fix it

### Guidelines

- Keep tools focused — one tool does one thing well
- Always handle exceptions and return a descriptive string — the LLM reads the error
- Test your tool standalone before wiring it into the agent
- Follow the existing code style — plain Python, no unnecessary abstractions

### Issues

Open an issue with: OS version, Python version, Ollama model name, and the exact error or unexpected behaviour. The more detail, the faster it gets resolved.

---

## Acknowledgements & Shoutouts

- **[Ollama](https://ollama.com)** — for making local LLM inference genuinely easy. Without this the whole project requires a cloud dependency.
- **[LangChain](https://python.langchain.com)** — the agent framework handling tool calling, conversation state, and the glue between the LLM and Python.
- **[Rich](https://github.com/Textualize/rich)** and **[Textual](https://github.com/Textualize/textual)** — for making terminal output actually look good.
- **[pycaw](https://github.com/AndreMiras/pycaw)** — the only sane way to control Windows audio from Python.
- **[screen-brightness-control](https://github.com/Crozzers/screen-brightness-control)** — handles the messy DDC/CI and WMI layers so you don't have to.
- **[psutil](https://github.com/giampaolo/psutil)** — reliable cross-platform process and system metrics.
- **[faster-whisper](https://github.com/SYSTRAN/faster-whisper)** — CTranslate2-based Whisper that runs comfortably on CPU with int8 quantization.
- **[watchdog](https://github.com/gorakhargosh/watchdog)** — clean file system event API that made the live index watcher trivial to implement.
- **[tree-sitter](https://tree-sitter.github.io/tree-sitter/)** — language-aware AST parsing that makes code chunking genuinely semantic rather than just line-splitting.
- **[PyMuPDF](https://pymupdf.readthedocs.io)** — fast and reliable PDF text extraction.
- **[pyautogui](https://pyautogui.readthedocs.io)** — global media key simulation that actually works.
- **[pygetwindow](https://github.com/asweigart/PyGetWindow)** — simple and effective window management.
- **[pywin32](https://github.com/mhammond/pywin32)** — the backbone for any serious Windows API work in Python.
- **[pyttsx3](https://github.com/nateshmbhat/pyttsx3)** — dead-simple offline TTS that runs entirely on-device via Windows SAPI; no model downloads, no API keys.

---

## A Note on This README

This README was written with the assistance of Claude (Anthropic), used as a research and writing partner throughout development. The project itself — the architecture decisions, tool implementations, debugging, and design — was done by the developer. The LLM helped articulate, structure, and document it. This is itself a demonstration of what thoughtful human + LLM collaboration looks like in a real project.

---

## License

MIT License — free to use, modify, and distribute. See `LICENSE` for details.

---

*Built on Windows. Runs locally. No cloud required.*