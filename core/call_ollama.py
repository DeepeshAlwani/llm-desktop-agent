from langchain.agents import create_agent
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markdown import Markdown
from rich.text import Text
import json
import re

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
    list_all_saved_profiles_names

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
           list_all_saved_profiles_names
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
                            
                        **CRITICAL**:
                        - Never invent or assume information not returned by a tool
                        - If a tool call fails or returns an error, report the exact error — do not explain why it might have failed
                        - Never describe actions you took unless a tool was actually called and returned a result
                        - If unsure, call the relevant tool to verify rather than reasoning from memory""",
                    
)
console = Console()

def _render_dict_as_table(data: dict):
    """Renders a dict as a two column key/value table"""
    table = Table(show_header=True, header_style="bold cyan", box=None)
    table.add_column("Setting", style="dim", width=20)
    table.add_column("Value", style="white")

    for key, value in data.items():
        if isinstance(value, list):
            value = ", ".join(str(v) if not isinstance(v, dict) 
                            else f"{v.get('name', '')} ({v.get('url', '')})" 
                            for v in value)
        table.add_row(str(key), str(value))

    console.print(table)


def _render_list(data: list):
    """Renders a list of items"""
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
    """
    Detects what kind of content the response contains
    and renders it appropriately with Rich
    """

    # detect json — profile contents, app lists etc
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

    # detect markdown table (agent sometimes outputs these)
    has_markdown = (
        ("|" in response_text and "---" in response_text) or
        re.search(r"^\d+\.", response_text, re.MULTILINE) or
        "**" in response_text or          # bold
        re.search(r"^#{1,3} ", response_text, re.MULTILINE) or  # headers
        re.search(r"^- ", response_text, re.MULTILINE)          # bullet lists
    )

    if has_markdown:
        console.print(Markdown(response_text))
        return

    # detect tool list (numbered list from agent)
    if re.search(r"^\d+\.", response_text, re.MULTILINE):
        console.print(Markdown(response_text))
        return

    # default — plain panel with the response
    console.print(Panel(
        response_text,
        border_style="blue",
        padding=(0, 1)
    ))


console.print(Panel.fit(
    "[bold white]LLM Desktop Agent[/bold white]\n[dim]Local AI control for Windows[/dim]",
    border_style="blue",
    padding=(1, 4)
))
console.print("[dim]Type 'exit' or 'quit' to stop.[/dim]\n")

conversation_history = []

while True:
    try:
        # styled input prompt
        user_input = console.input("[bold blue]You:[/bold blue] ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]Goodbye![/dim]")
        break

    if not user_input:
        continue

    if user_input.lower() in ("exit", "quit"):
        console.print("[dim]Goodbye![/dim]")
        break

    conversation_history.append({"role": "user", "content": user_input})

    # show a spinner while agent is thinking
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

    # render with Rich instead of plain print
    console.print("[bold green]Assistant:[/bold green]")
    render_response(response_text)
    console.print()
