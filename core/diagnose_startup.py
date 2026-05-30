"""
diagnose_startup.py
-------------------
Run this standalone (no agent, no Ollama) to find exactly what's slow.

    python diagnose_startup.py

Each phase is timed independently and a summary table is printed at the end.
Phases that take > 5 s are highlighted.
"""

import time
import os
import sys

results: list[tuple[str, float]] = []

def timed(label: str):
    """Context manager — prints and records elapsed time for a block."""
    class _Timer:
        def __enter__(self):
            self._t = time.perf_counter()
            print(f"  ▶ {label} ...", flush=True)
            return self
        def __exit__(self, *_):
            elapsed = time.perf_counter() - self._t
            results.append((label, elapsed))
            mark = "🔴" if elapsed > 5 else ("🟡" if elapsed > 1 else "✅")
            print(f"  {mark} {label}: {elapsed:.2f}s", flush=True)
    return _Timer()


print("\n" + "="*60)
print("  Startup Diagnostic")
print("="*60 + "\n")

# ── Phase 1: Python imports ───────────────────────────────────────────────────
print("[Phase 1] Python imports")

with timed("import numpy"):
    import numpy as np

with timed("import sounddevice"):
    import sounddevice as sd

with timed("import faster_whisper (module only, no model)"):
    try:
        from faster_whisper import WhisperModel
        fw_available = True
    except ImportError:
        fw_available = False
        print("    ⚠ faster-whisper not installed")

with timed("import rich"):
    from rich.console import Console
    from rich.table import Table

with timed("import prompt_toolkit"):
    from prompt_toolkit import prompt

print()

# ── Phase 2: Whisper model loading ───────────────────────────────────────────
print("[Phase 2] Whisper model loading (this is likely the culprit)")

import glob

# ── Check every known cache location faster-whisper / huggingface might use ──
search_roots = [
    os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub"),
    os.path.join(os.path.expanduser("~"), ".cache", "huggingface"),
    os.path.join(os.path.expanduser("~"), ".cache", "whisper"),
    os.path.join(os.path.expanduser("~"), ".cache", "faster_whisper"),
    os.path.join(os.path.expanduser("~"), ".cache", "ctranslate2"),
    # Windows-specific
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "huggingface"),
    os.path.join(os.environ.get("APPDATA", ""), "huggingface"),
]

print("  Scanning known cache locations:")
for root in search_roots:
    exists = os.path.isdir(root)
    if exists:
        entries = glob.glob(os.path.join(root, "**", "*tiny*"), recursive=True)
        print(f"    ✅ EXISTS  {root}")
        if entries:
            for e in entries[:5]:
                size = os.path.getsize(e) if os.path.isfile(e) else 0
                print(f"       📄 {e}  ({size/1024/1024:.1f} MB)")
        else:
            print(f"       (no 'tiny' files found)")
    else:
        print(f"    ❌ missing {root}")

# ── Intercept the actual download path by patching huggingface_hub ────────────
print()
print("  Monkey-patching huggingface_hub.snapshot_download to log exact paths...")
_actual_paths_used: list[str] = []
try:
    import huggingface_hub
    _orig_snapshot = huggingface_hub.snapshot_download
    def _patched_snapshot(*args, **kwargs):
        result = _orig_snapshot(*args, **kwargs)
        _actual_paths_used.append(str(result))
        print(f"    📥 snapshot_download → {result}")
        return result
    huggingface_hub.snapshot_download = _patched_snapshot

    # Also patch the lower-level hf_hub_download
    _orig_hf = huggingface_hub.hf_hub_download
    def _patched_hf(*args, **kwargs):
        result = _orig_hf(*args, **kwargs)
        _actual_paths_used.append(str(result))
        print(f"    📥 hf_hub_download → {result}")
        return result
    huggingface_hub.hf_hub_download = _patched_hf
except Exception as e:
    print(f"    ⚠ Could not patch huggingface_hub: {e}")

if fw_available:
    with timed("WhisperModel('tiny', cpu, int8) — FIRST/CACHED"):
        m_tiny = WhisperModel("tiny", device="cpu", compute_type="int8")

    if _actual_paths_used:
        print(f"  ℹ Model was DOWNLOADED (not cached) to:")
        for p in _actual_paths_used:
            print(f"    {p}")
        # Check if the path actually has files after load
        for p in _actual_paths_used:
            base = os.path.dirname(p) if os.path.isfile(p) else p
            if os.path.isdir(base):
                files = os.listdir(base)
                print(f"    Files in that dir: {files}")
    else:
        print(f"  ℹ No download calls intercepted — model loaded from disk cache")

    with timed("WhisperModel('base', cpu, int8) — FIRST/CACHED"):
        m_base = WhisperModel("base", device="cpu", compute_type="int8")

    # Test actual transcription speed on a dummy clip
    with timed("tiny.transcribe (1s silence clip — warm-up)"):
        dummy = np.zeros(16000, dtype="float32")
        list(m_tiny.transcribe(dummy, language="en")[0])  # consume generator

    with timed("base.transcribe (1s silence clip — warm-up)"):
        list(m_base.transcribe(dummy, language="en")[0])

print()

# ── Phase 3: memory / DB init ─────────────────────────────────────────────────
print("[Phase 3] memory.init_db()")
try:
    with timed("memory.init_db()"):
        import memory
        memory.init_db()
except Exception as e:
    print(f"  ⚠ skipped (import error: {e})")

print()

# ── Phase 4: file DB init ─────────────────────────────────────────────────────
print("[Phase 4] init_file_db()")
try:
    with timed("init_file_db()"):
        from file_manager import init_file_db
        init_file_db()
except Exception as e:
    print(f"  ⚠ skipped (import error: {e})")

print()

# ── Phase 5: watchdog observer ────────────────────────────────────────────────
print("[Phase 5] watchdog Observer start")
try:
    with timed("Observer().start()"):
        from file_manager import WATCHED_FOLDER, AgentFileHandler
        from watchdog.observers import Observer
        obs = Observer()
        obs.schedule(AgentFileHandler(), WATCHED_FOLDER, recursive=True)
        obs.start()
        obs.stop()
except Exception as e:
    print(f"  ⚠ skipped (import error: {e})")

print()

# ── Phase 6: Parallelism check ────────────────────────────────────────────────
print("[Phase 6] Parallel model load (simulates your _model_executor approach)")
if fw_available:
    import concurrent.futures

    def _load_tiny():
        return WhisperModel("tiny", device="cpu", compute_type="int8")

    with timed("WhisperModel('tiny') via ThreadPoolExecutor (already cached)"):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_load_tiny)
            result = fut.result()

print()

# ── Summary ───────────────────────────────────────────────────────────────────
print("="*60)
print("  SUMMARY")
print("="*60)
total = sum(t for _, t in results)
for label, t in sorted(results, key=lambda x: x[1], reverse=True):
    bar = "█" * min(int(t * 2), 40)
    mark = "🔴" if t > 5 else ("🟡" if t > 1 else "✅")
    print(f"  {mark} {t:6.2f}s  {bar}  {label}")
print(f"\n  Total measured: {total:.2f}s")
print()
print("DIAGNOSIS:")
slow = [(l, t) for l, t in results if t > 5]
if slow:
    for l, t in slow:
        print(f"  🔴 SLOW ({t:.1f}s): {l}")
    print()
    print("  If 'WhisperModel tiny/base' are slow AND cache entries exist,")
    print("  the bottleneck is CTranslate2 model initialisation on your CPU.")
    print("  → Try: compute_type='int8_float16' if your CPU supports AVX512")
    print("  → Or:  device='cuda' if you have a CUDA GPU available to faster-whisper")
    print()
    print("  To check CUDA support in faster-whisper:")
    print("    python -c \"from faster_whisper import WhisperModel; m = WhisperModel('tiny', device='cuda')\"")
else:
    print("  ✅ No single phase over 5s — startup should feel fast.")