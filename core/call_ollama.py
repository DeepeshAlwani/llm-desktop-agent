from langchain.agents import create_agent

from tools import (
    volume_control,
    mute_device,
    pause_media,
    set_active_window,
    get_current_volume,
    get_screen_brightness,
    adjust_screen_brightness,
    save_profile
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
           save_profile
           ],
    system_prompt="""You are a Windows computer control assistant.
                    You have tools to control this computer's audio.

                    IMPORTANT RULES:
                    - Only call a tool when the user explicitly asks you to perform an action
                    - If the user asks what tools you have, describe them from their descriptions — do NOT call them
                    - Never test or demonstrate a tool unless asked to perform that action
                    - Listing tools = describe them in text only""",
)

print("Computer Control Assistant")
print("Type 'exit' or 'quit' to stop.\n")

conversation_history = []

while True:
    try:
        user_input = input("You: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nGoodbye!")
        break

    if not user_input:
        continue

    if user_input.lower() in ("exit", "quit"):
        print("Goodbye!")
        break

    conversation_history.append({"role": "user", "content": user_input})

    result = agent.invoke({"messages": conversation_history})

    assistant_message = result["messages"][-1]
    blocks = getattr(assistant_message, "content_blocks", None)
    response_text = blocks[0]["text"] if blocks else str(assistant_message.content)


    response_text = assistant_message.content_blocks[0]["text"]

    conversation_history.append({"role": "assistant", "content": response_text})

    print(f"Assistant: {response_text}\n")