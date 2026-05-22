from langchain.agents import create_agent

from tools import volume_control

def get_weather(city: str) -> str:
    """Get weather for a given city."""
    return f"It's always sunny in {city}!"

agent = create_agent(
    model="ollama:granite4.1:8b",
    tools=[get_weather, volume_control],
    system_prompt="You are a helpful assistant",
)

result = agent.invoke(
    {"messages": [{"role": "user", "content": "can you adjust the volume to 55% please thank you"}]}
)
print(result["messages"][-1].content_blocks[0]['text'])