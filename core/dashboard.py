# dashboard.py
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Sparkline, DataTable, Label, ProgressBar
from textual.containers import Horizontal, Vertical, ScrollableContainer
import psutil
import time


try:
    import GPUtil
    GPU_AVAILABLE = True
except ImportError:
    GPU_AVAILABLE = False


class MetricCard(Vertical):
    DEFAULT_CSS = """
    MetricCard {
        border: solid $primary;
        padding: 0 1;
        height: 9;
        margin: 0 1 1 0;
    }
    MetricCard .card-title {
        color: $text-muted;
        height: 1;
    }
    MetricCard .value {
        color: $success;
        text-style: bold;
        height: 1;
    }
    MetricCard Sparkline {
        height: 4;
    }
    """

    def __init__(self, title: str, unit: str = "%", **kwargs):
        super().__init__(**kwargs)
        self.title = title
        self.unit = unit
        self._history = [0.0] * 40

    def compose(self) -> ComposeResult:
        yield Label(self.title, classes="card-title")
        yield Label("0" + self.unit, classes="value", id=f"{self.id}_value")
        yield Sparkline(self._history, id=f"{self.id}_spark")

    def update_value(self, value: float):
        self._history = self._history[1:] + [value]
        self.query_one(f"#{self.id}_value", Label).update(
            f"{value:.1f}{self.unit}"
        )
        self.query_one(f"#{self.id}_spark", Sparkline).data = self._history


class ProcessTable(ScrollableContainer):
    DEFAULT_CSS = """
    ProcessTable {
        border: solid $primary;
        margin: 0 0 1 0;
        padding: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield DataTable(id="proc_table")

    def on_mount(self):
        table = self.query_one(DataTable)
        table.add_columns("PID", "Name", "CPU %", "RAM MB", "Status")
        table.cursor_type = "row"

    def refresh_processes(self):
        table = self.query_one(DataTable)
        table.clear()

        procs = []
        for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info', 'status']):
            try:
                procs.append(p.info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        procs.sort(key=lambda x: x['cpu_percent'] or 0, reverse=True)

        for p in procs[:30]:
            ram_mb = round(p['memory_info'].rss / 1024 / 1024, 1) if p['memory_info'] else 0
            cpu = p['cpu_percent'] or 0
            cpu_str = f"[red]{cpu:.1f}[/red]" if cpu > 50 else (
                f"[yellow]{cpu:.1f}[/yellow]" if cpu > 20 else f"{cpu:.1f}"
            )
            table.add_row(
                str(p['pid']),
                p['name'][:28],
                cpu_str,
                str(ram_mb),
                p['status']
            )


class SystemDashboard(App):
    TITLE = "LLM Desktop Agent — System Monitor"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh_procs", "Refresh"),
        ("d", "toggle_dark", "Theme"),
    ]

    CSS = """
    Screen {
        layout: vertical;
    }
    #metrics_row {
        layout: horizontal;
        height: 10;
        margin: 1 0 0 1;
    }
    #battery_row {
        height: 3;
        margin: 0 1 1 1;
        padding: 0 1;
        border: solid $warning;
        layout: horizontal;
    }
    #battery_label {
        width: 1fr;
    }
    #battery_bar {
        width: 2fr;
        margin-top: 1;
    }
    """

    _prev_disk = None
    _prev_disk_time = None

    def compose(self) -> ComposeResult:
        yield Header()

        with Horizontal(id="metrics_row"):
            yield MetricCard("CPU", unit="%", id="cpu_card")
            yield MetricCard("RAM", unit="%", id="ram_card")
            yield MetricCard("GPU", unit="%", id="gpu_card")
            yield MetricCard("GPU RAM", unit="%", id="gpu_ram_card")
            yield MetricCard("Disk Read", unit=" MB/s", id="disk_r_card")
            yield MetricCard("Disk Write", unit=" MB/s", id="disk_w_card")

        with Horizontal(id="battery_row"):
            battery = psutil.sensors_battery()
            if battery:
                yield Label("🔋", id="battery_label")
                yield ProgressBar(total=100, id="battery_bar")
            else:
                yield Label("🔌 Desktop — no battery")

        yield ProcessTable(id="proc_container")
        yield Footer()

    def on_mount(self):
        self._prev_disk = psutil.disk_io_counters()
        self._prev_disk_time = time.monotonic()
        self.refresh_metrics()
        self.refresh_processes()
        self.set_interval(2, self.refresh_metrics)
        self.set_interval(5, self.refresh_processes)

    def refresh_metrics(self):
        # CPU
        self.query_one("#cpu_card", MetricCard).update_value(
            psutil.cpu_percent(interval=None)
        )

        # RAM
        self.query_one("#ram_card", MetricCard).update_value(
            psutil.virtual_memory().percent
        )

        # GPU'
        if GPU_AVAILABLE:
            try:
                gpus = GPUtil.getGPUs()
                if gpus:
                    self.query_one("#gpu_card", MetricCard).update_value(gpus[0].load * 100)
                    total = gpus[0].memoryTotal or 1
                    used = gpus[0].memoryUsed or 0
                    self.query_one("#gpu_ram_card", MetricCard).update_value(
                        round(used / total * 100, 1)
                    )
            except Exception:
                pass
        else:
            self.query_one("#gpu_card", MetricCard).update_value(0)
            self.query_one("#gpu_ram_card", MetricCard).update_value(0)

        # Disk delta MB/s
        disk = psutil.disk_io_counters()
        now = time.monotonic()
        if disk and self._prev_disk and self._prev_disk_time:
            elapsed = now - self._prev_disk_time
            if elapsed > 0:
                read_mbs = (disk.read_bytes - self._prev_disk.read_bytes) / elapsed / 1024 / 1024
                write_mbs = (disk.write_bytes - self._prev_disk.write_bytes) / elapsed / 1024 / 1024
                self.query_one("#disk_r_card", MetricCard).update_value(max(0, read_mbs))
                self.query_one("#disk_w_card", MetricCard).update_value(max(0, write_mbs))
        self._prev_disk = disk
        self._prev_disk_time = now

        # Battery
        battery = psutil.sensors_battery()
        if battery:
            pct = int(battery.percent)
            plugged = "🔌" if battery.power_plugged else "🔋"
            mins = int(battery.secsleft / 60) if battery.secsleft > 0 and not battery.power_plugged else 0
            time_str = f"  {mins}min remaining" if mins > 0 else ""
            self.query_one("#battery_label", Label).update(
                f"{plugged} {pct}%{time_str}"
            )
            self.query_one("#battery_bar", ProgressBar).progress = pct

    def refresh_processes(self):
        self.query_one("#proc_container", ProcessTable).refresh_processes()

    def action_refresh_procs(self):
        self.refresh_processes()


if __name__ == "__main__":
    SystemDashboard().run()