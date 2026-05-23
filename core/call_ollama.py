from langchain.agents import create_agent

from tools import volume_control, mute_device, pause_media, set_active_window

def get_weather(city: str) -> str:
    """Get weather for a given city."""
    return f"It's always sunny in {city}!"

agent = create_agent(
    model="ollama:granite4.1:8b",
    tools=[get_weather, volume_control, mute_device, pause_media, set_active_window],
    system_prompt="""You are a Windows computer control assistant.
                    You have tools to control this computer's audio.

                    IMPORTANT RULES:
                    - Only call a tool when the user explicitly asks you to perform an action
                    - If the user asks what tools you have, describe them from their descriptions — do NOT call them
                    - Never test or demonstrate a tool unless asked to perform that action
                    - Listing tools = describe them in text only""",
)

result = agent.invoke(
    {"messages": [{"role": "user", "content": "can you make explorer the active windows"}]}
)
print(result["messages"][-1].content_blocks[0]['text'])