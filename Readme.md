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

`llm-desktop-agent` is a fully local AI agent that lets you control your Windows machine through natural language. Ask it to adjust your volume, change screen brightness, open apps, manage profiles, close processes, query system info, read and write files in your workspace, create presentations, or search the web — all without touching the cloud.

It uses [Ollama](https://ollama.com) to run LLMs locally on your hardware and [LangGraph](https://langchain-ai.github.io/langgraph/) to orchestrate a **multi-agent system**. Rather than a single-shot router, a supervisor first **plans** your request into an ordered list of agent steps, executes them one by one, has a **judge** grade each step's output and retry it with feedback if it falls short, and runs a **final review** against your original goal before the turn ends — with a full markdown transcript of the plan, every step, and every verdict logged for each turn. The terminal interface is built with [Rich](https://github.com/Textualize/rich) for clean formatted output, and a live system monitor is available via [Textual](https://github.com/Textualize/textual).

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

Files are indexed into SQLite using [nomic-embed-text-v2-moe](https://ollama.com/library/nomic-embed-text) embeddings (upgraded from `nomic-embed-text`) and watched automatically via [watchdog](https://github.com/gorakhargosh/watchdog) — any file change on disk (created, modified, deleted) updates the index in real time without restarting the agent.

The `write_file` tool also supports writing **`.docx` Word documents** directly from markdown. When the content contains headings (`#`), bullets (`-`), numbered lists, or markdown tables, `file_manager.write_docx` converts them to proper Word formatting — including bold/italic/underline runs, native table grids, and list styles. This means the agent can produce structured Word documents without any extra instructions.

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
- **Multi-step planning** — `supervisor_node` no longer just routes a request to one agent; it plans it into an ordered list of steps, each assigned to the specialist agent best suited to handle it, based on the user's overall intent
- **Quality-judged execution** — every step's output is graded by `judge_node`; a failing step is retried by the same agent (up to 3 attempts) with the judge's feedback appended, and if it still fails after 3 tries the supervisor is called back in to reframe the step, hand it to a different agent, or tell the user the task couldn't be completed
- **Final review** — once every planned step is done, `final_review_node` checks the overall result against the user's original goal; if it falls short, the plan is regenerated from scratch with feedback about what went wrong, rather than silently returning an incomplete answer
- **Context folding between steps** — `plan_utils` carries forward the relevant results of earlier steps into later ones in the same plan, so a multi-agent task (e.g. research → write file → build slides) flows with shared context instead of each agent working blind
- **Per-turn run transcripts** — `run_logger` writes a detailed markdown log for every user turn, capturing the generated plan, each agent's raw output, every judge verdict (and retries), and the final review outcome — useful for debugging and understanding exactly how a response was produced
- **Profile system** — save and load named configurations with multiple apps, optional URLs per app, volume and brightness (e.g. "study", "gaming", "focus")
- **Multi-step reasoning** — one request chains multiple tools automatically within the responsible agent
- **System awareness** — checks if apps are already running before opening, detects tray vs visible windows, verifies actions with follow-up tool calls
- **Web search** — `rag_agent` searches the web via SearXNG + Wikipedia fallback and returns results inline
- **CMD access** — controlled read/write access to Windows command line split into two tools: read-only queries (`query_system`) and state-changing commands (`run_system_command`) with mandatory user confirmation
- **Command safety layer** — blocklist of destructive operations (`del`, `format`, `reg delete`, `diskpart`, symlink creation, script file writes, etc.) refused regardless of how they're requested; separate confirmation gate for shutdown, restart, `winget install/uninstall`, and network changes
- **Persistent memory** — SQLite-backed conversation history with semantic retrieval; past relevant exchanges are injected into context automatically using cosine similarity scoring
- **Context window tracking** — tiktoken token count is displayed before every agent call, colour-coded green/yellow/red and showing tokens remaining against the model's 131,072-token context window
- **Centralized model configuration** — `core/config.py` holds the model name and context-window (`num_ctx`) settings used across agents, instead of each node hardcoding its own

### Presentation Creation

The agent can generate fully-designed `.pptx` files autonomously. Say things like _"make me a presentation on climate change"_ or _"create a 7-slide deck on Python for beginners"_ and it will produce a polished file in your workspace.

- **`call_ppt_agent`** — a dedicated sub-agent (`ppt_agent.py`) runs the entire pipeline independently
- The LLM outputs a structured **JSON spec** (previously XML — switched for more reliable parsing) containing a custom colour palette and slide-by-slide content, and each step is graded by the judge before the deck is finalized
- **7 available layouts**: `title`, `section`, `content`, `two_column`, `image_right`, `big_stat`, `closing`
- **Per-element rich text** — every heading, subheading, and bullet item supports `bold`, `italic`, `size`, and `align` attributes
- **LLM-generated palettes** — the model invents a colour scheme that matches the topic's mood (e.g. saffron + green for India, teal + deep blue for ocean/science)
- **Automatic images** — image elements fetch real photos via Pixabay and Unsplash (free API keys required; see `.env` setup); a solid-colour placeholder is used if keys are absent or a download fails
- **`pill_label` overrides** — each slide can display a small topic-specific tab label (e.g. "TIMELINE", "KEY FIGURES") instead of a generic one
- **Web-researched content** — the sub-agent searches the web via SearXNG before writing each slide, producing detailed factual bullet points rather than shallow summaries; falls back to Wikipedia automatically if the SearXNG instance is unavailable
- The finished `.pptx` is saved to `agent_workspace/` and indexed for semantic search automatically

```
You: make a presentation on the history of computing
Assistant: Designed 7 slides with custom palette → Saved to agent_workspace/history_of_computing.pptx
           Want me to open it?
```

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
| [LangChain](https://python.langchain.com) | Tool definitions, LLM bindings, message schema |
| [LangGraph](https://langchain-ai.github.io/langgraph/) | Multi-agent graph: supervisor + specialist nodes |
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
| [python-docx](https://python-docx.readthedocs.io) | Word document reading and markdown→docx writing |
| [python-pptx](https://python-pptx.readthedocs.io) | PowerPoint reading and PPTX generation (via ppt_renderer) |
| [tiktoken](https://github.com/openai/tiktoken) | Token counting for context window tracking |
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

## Environment Variables

Create a `.env` file in the project root with the following keys. All are optional — the agent runs without them but with reduced capability.

```env
# Image search for presentations (free accounts, no credit card)
# Pixabay:  https://pixabay.com/api/docs/
# Unsplash: https://unsplash.com/oauth/applications  (use the Access Key, not the Secret Key)
PIXABAY_API_KEY=your_pixabay_key
UNSPLASH_ACCESS_KEY=your_unsplash_access_key

# Web search for presentation content (optional — falls back to Wikipedia if not set)
# Self-host: https://docs.searxng.org/admin/installation.html
# Or use a public instance: https://searx.be
SEARXNG_URL=https://searx.be
```

If image keys are not set, presentation image slides will render as solid-colour placeholders. If `SEARXNG_URL` is not set or the instance is unavailable, the agent falls back to Wikipedia for content research.

---

## Known Issues & Technical Notes

### Ollama Context Window — Critical for `ppt_agent`

Ollama's default context window is **4096 tokens** regardless of the model's actual capability. For `granite4.1:8b` (which supports 131,072 tokens), this means the model silently truncates its context to 4096 tokens unless explicitly overridden.

**Symptom:** The PPT sub-agent asks the same clarifying question repeatedly, forgetting answers it received just one round earlier. Token counts in the console stay stuck at exactly 4096 (`prompt_eval_count: 4096`) no matter how long the conversation grows.

**Cause:** `ChatOllama` defaults to `num_ctx=4096`. With `ppt_knowledge.md` already filling most of that budget, there is almost no room left for conversation history.

**Fix applied:** `ChatOllama` is now initialised with an explicit `num_ctx`:

```python
from langchain_ollama import ChatOllama
llm = ChatOllama(model=MODEL, num_ctx=NUM_CTX*2)
```

16,384 tokens is more than sufficient for the clarification loop and leaves comfortable headroom. Using the full 131,072 is possible but significantly increases VRAM usage — not recommended unless you have 16GB+ VRAM.

If you swap to a different model, check its actual context window in the Ollama model card and set `num_ctx` accordingly. The symptom of repeated questions almost always means the context is being truncated.

### DuckDuckGo Web Search — Replaced

The original `web_search` tool used `duckduckgo_search`. DuckDuckGo aggressively blocks automated requests, returning "No results found" on almost every query. The package was also renamed to `ddgs` without a deprecation period, causing import warnings.

**Fix applied:** Replaced with SearXNG (aggregates Google, Bing, Wikipedia) with a Wikipedia fallback. SearXNG works out of the box via the public instance at `https://searx.be` with no API key. Heavy users should self-host.

### Image Search — Replaced

The original renderer fetched images via `source.unsplash.com` — a deprecated Unsplash endpoint that no longer works. Every image element silently fell back to a solid dark-blue rectangle.

**Fix applied:** `ppt_renderer.py` now delegates all image fetching to `image_search.py`, which uses the official Pixabay and Unsplash APIs with proper authentication. The deprecated URL has been removed entirely.

### Diagnosing slow startup

If the agent takes a long time to start, run the standalone diagnostic:

```bash
python diagnose_startup.py
```

This times each startup phase independently (Python imports, Whisper model loading, DB init, watchdog observer start, etc.) and prints a colour-coded summary table highlighting any phase over 5 seconds.

---

## Voice Input

The assistant uses always-on voice activation — just say **"hello"** to wake it up and start speaking your query.

- Runs entirely on CPU using faster-whisper with **two optimized models**:
  - `small` model (int8) for wake-word detection (`"hello"`) — upgraded from `tiny` for better accuracy
  - `base` model (int8) for accurate transcription of the actual query
- The Whisper models download and load automatically on first use (~200MB combined, cached afterwards)
- The wake model loads in a background thread (`ThreadPoolExecutor`) at startup so it is ready before you speak
- No hotkeys required — voice activation is fully hands-free
- You can change the wake word by editing `WAKE_PHRASE` in `call_ollama.py`
- You can change the model sizes (`WAKE_MODEL_SIZE` / `COMMAND_MODEL_SIZE`) in `call_ollama.py`
- Voice support remains optional — if the required packages are not installed, the agent automatically falls back to text-only mode without errors

> **Note:** `VOICE_AVAILABLE` is currently set to `False` in `call_ollama.py` to force text-only mode. Set it back to `True` (and remove the override line) to re-enable voice.

---

## Project Structure

```
llm-desktop-agent/
├── core/
│   ├── call_ollama.py          # Agent loop, conversation management, Rich rendering, voice input
│   ├── config.py               # Centralized per-agent model name and context-window (num_ctx) settings
│   ├── tools.py                # All LangChain tool definitions
│   ├── plan_utils.py           # Plan state management, step advancement, context folding between steps
│   ├── run_logger.py           # Writes a markdown transcript (plan, steps, judge verdicts) per user turn
│   ├── agents/
│   │   ├── graph.py            # LangGraph StateGraph — wires all nodes into a compiled graph
│   │   ├── supervisor_node.py  # Planner — turns each request into an ordered list of agent steps
│   │   ├── judge_node.py       # Grades judgable agent output; drives retries with feedback
│   │   ├── final_review_node.py# Reviews the completed plan against the original user goal
│   │   ├── ppt_agent_node.py   # PowerPoint creation pipeline (JSON spec, judge-reviewed)
│   │   ├── window_agent_node.py# Desktop control: audio, brightness, apps, window layout, profiles
│   │   ├── shell_agent_node.py # Shell/CMD access: queries and state-changing commands
│   │   ├── file_agent_node.py  # Workspace file CRUD, search by name/extension
│   │   ├── web_search_agent_node.py  # Web_search 
│   │   ├── rag_agent_node.py   # Semantic file search
│   │   └── general_result_agent_node.py  # Conversation, Q&A, capability help
│   ├── file_manager.py         # File reading/writing, markdown→docx, indexing, tree-sitter, watchdog
│   ├── memory.py               # SQLite conversation history and semantic memory retrieval
│   ├── image_search.py         # Pixabay + Unsplash image search with base64 download
│   ├── ppt_renderer.py         # Standalone PPTX renderer; hex palette + rich-text per element
│   ├── ppt_knowledge.md        # Reference knowledge injected into the PPT agent context
│   ├── dashboard.py            # Textual live system monitor (launched separately)
│   └── diagnose_startup.py     # Startup phase timer — run standalone to find slow imports
├── profiles/                   # Saved user profiles — auto-created on first save
├── agent_workspace/            # Sandboxed folder the agent can read/write (on Desktop)
│   └── images/                 # Auto-created; caches Pixabay/Unsplash images for presentations
├── agent_files.db              # SQLite index for workspace files and embeddings
├── agent_memory.db             # SQLite store for conversation history and embeddings
├── assets/
│   └── preview.png             # Terminal screenshot for README
├── requirements.txt
└── README.md
```

Every user request is planned into an ordered list of steps up front, and each step is dispatched to exactly one specialist agent. Each agent still operates with its own focused system prompt and tool set — no agent sees tools it doesn't need — which keeps context windows lean and each step's execution accurate. Every step's result then passes through the judge before the plan is allowed to advance.

---

## How It Works

```
User input (typed or voice)
        ↓
   Unified input queue
   (keyboard thread + voice thread)
        ↓
   Context window tracking
   (tiktoken token count displayed before each call)
        ↓
   Semantic memory retrieval
   (past relevant exchanges injected into context)
        ↓
   supervisor_node — PLANNER
   (breaks the request into an ordered list of steps,
    each assigned to a specialist agent)
        ↓
   ┌────────────────────── For each step in the plan ──────────────────────┐
   │                                                                        │
   │   ┌──────────┬──────────────┬─────────────┬───────────┬──────────┬───────────┐
   │   ppt_agent  window_agent  shell_agent  file_agent  rag_agent  web_search   general_agent
   │        ↓                                                                     │
   │   Agent executes its tools:                                                  │
   │   pycaw / pyautogui / subprocess / sbc / psutil / win32api / win32gui        │
   │   file_manager / sqlite / watchdog / tree-sitter / ollama embeddings         │
   │   ppt_renderer (presentation pipeline)                                       │
   │        ↓                                                                     │
   │   judge_node — grades the step's output                                     │
   │        ↓                                                            ┌─ fail ─┤
   │      pass                                                           │        │
   │        ↓                                              retry same agent       │
   │   plan_utils folds this step's result                  with judge feedback   │
   │   into context for the next step                       (up to 3 attempts)   │
   │        ↓                                                                     │
   │   (still failing after 3 tries → back to supervisor_node to reframe          │
   │    the step, hand it to a different agent, or report failure to the user)   │
   └────────────────────────────────────────────────────────────────────────────┘
        ↓
   final_review_node
   (checks the completed plan against the ORIGINAL user goal)
        ↓
      fail → re-plan from scratch, with feedback on what went wrong ──┐
        ↓ pass                                                        │
   run_logger writes the full markdown transcript                     │
   (plan, every step, every judge verdict, final review) ←────────────┘
        ↓
   Rich-formatted response to user
```

The LLM never directly touches your system or files. It outputs structured tool calls, and Python executes them. Every action is inspectable, restrictable, and extensible — and now every step of that process is planned, graded, and logged before the final answer reaches you.

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

## Presentation Creation

The agent has a dedicated sub-agent (`ppt_agent.py`) that generates fully-designed PowerPoint files. Just describe what you want:

```
You: make me a 7-slide presentation on machine learning
You: create a deck on Indian Independence Day with a festive theme
You: build a presentation on our Q3 results, save it as q3_review.pptx
```

The PPT pipeline:

1. The sub-agent (`ppt_agent.py`) asks up to 2 clarifying questions to understand audience, topic depth, and format — then proceeds automatically
2. The LLM searches the web via SearXNG (with Wikipedia fallback) to gather factual content before writing each slide
3. The LLM produces a structured JSON spec with a custom colour palette and one slide object per slide, with per-element rich text formatting
4. Images are fetched via Pixabay and Unsplash using `image_search.py` and embedded directly into the `.pptx`
5. `ppt_renderer.py` converts the spec into a proper `.pptx` file using python-pptx, with automatic contrast violation fixes
6. The finished file is saved to `agent_workspace/` and indexed for semantic search

**Available layouts:** `title` · `section` · `content` · `two_column` · `image_right` · `big_stat` · `closing`

The LLM designs the palette for each topic: saffron and green for India, teal and deep navy for science, crimson and black for drama. Every heading, subheading, and bullet item can carry its own `bold`, `italic`, `size`, and `align` — the LLM controls per-element formatting, not just slide-level styles.

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
- [✔️] Voice feedback — TTS responses so the agent speaks back
- [✔️] Web search tool — agent can search the web inline
- [✔️] Presentation creation — full `.pptx` generation with LLM-designed palettes, SearXNG/Wikipedia web research, and Pixabay/Unsplash images
- [✔️] Markdown → Word document writing (headings, bullets, tables via python-docx)
- [✔️] Context window tracking — token count displayed per turn with colour-coded usage
- [✔️] Multi-step planning — supervisor plans requests into an ordered list of agent steps instead of single-shot routing
- [✔️] Quality judging with retries — judge_node grades each step and retries with feedback before escalating back to the supervisor
- [✔️] Final review against the original goal — final_review_node can trigger a full re-plan if the completed plan falls short
- [✔️] Per-turn markdown run transcripts (plan, steps, judge verdicts, final review) via run_logger
- [✔️] Centralized model/config settings via core/config.py
- [ ] Night light toggle via Windows registry
- [ ] Resolution switching
- [ ] System shutdown / restart / sleep commands
- [ ] WhatsApp and other UWP app process name alias map
- [✔️] Per-app volume control (set Spotify to 40% without touching system volume)

### Medium Term
- [ ] Scheduled actions ("mute at 11pm every night") via APScheduler
- [ ] Snapshot and restore system state before profile apply
- [ ] Multi-monitor support — specify which display to move a window to
- [ ] Multi-monitor brightness — set brightness per display independently
- [ ] AMD GPU monitoring support
- [ ] Clipboard read/write — "copy this text to my clipboard" or "what's in my clipboard"

### Long Term
- [ ] Native Windows GUI using PySide6 (no Electron, no web wrapper)
- [✔️] LangGraph multi-agent system — supervisor plans and dispatches to specialist agents (ppt, window, shell, file, rag, web_search, general), with judge and final-review quality control
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

- **New tools** — anything controllable via Python on Windows. Add in `tools.py` with the `@tool` decorator and a clear docstring, then wire it into the appropriate agent node.
- **New agents** — have a capability that doesn't fit any existing agent? Add a new node in `agents/`, register it in `graph.py`, teach `supervisor_node.py`'s planner to assign it steps, and add it to `judge_node.py`'s judgable list if its output benefits from quality grading and retries.
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
- **[LangChain](https://python.langchain.com)** — tool definitions, LLM bindings, and the message schema that ties everything together.
- **[LangGraph](https://langchain-ai.github.io/langgraph/)** — the multi-agent graph framework that replaced the original monolithic agent. The supervisor + specialist node pattern made routing clean and each agent's context minimal.
- **[Rich](https://github.com/Textualize/rich)** and **[Textual](https://github.com/Textualize/textual)** — for making terminal output actually look good.
- **[pycaw](https://github.com/AndreMiras/pycaw)** — the only sane way to control Windows audio from Python.
- **[screen-brightness-control](https://github.com/Crozzers/screen-brightness-control)** — handles the messy DDC/CI and WMI layers so you don't have to.
- **[psutil](https://github.com/giampaolo/psutil)** — reliable cross-platform process and system metrics.
- **[faster-whisper](https://github.com/SYSTRAN/faster-whisper)** — CTranslate2-based Whisper that runs comfortably on CPU with int8 quantization.
- **[watchdog](https://github.com/gorakhargosh/watchdog)** — clean file system event API that made the live index watcher trivial to implement.
- **[tree-sitter](https://tree-sitter.github.io/tree-sitter/)** — language-aware AST parsing that makes code chunking genuinely semantic rather than just line-splitting.
- **[python-pptx](https://python-pptx.readthedocs.io)** — the backbone of the PPTX generation pipeline, powering every shape, textbox, colour fill, and image embed in the presentation renderer.
- **[tiktoken](https://github.com/openai/tiktoken)** — fast BPE tokeniser used for context window counting, so the agent can show how much of the model's context is in use before each call.
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