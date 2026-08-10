import json, os, subprocess, sys
from anthropic import Anthropic
from anthropic.types import Message
from dotenv import load_dotenv

load_dotenv(override=True)

if not os.environ.get("ANTHROPIC_API_KEY"):
    sys.exit("ANTHROPIC_API_KEY not set — add it to .env (see .env.sample)")

client = Anthropic()
model = "claude-sonnet-5"

# A confused model can loop until the budget is gone. 02-beginner.md calls a turn
# limit "the first thing to add".
MAX_TURNS = 25

SYSTEM_PROMPT = """You are an autonomous terminal coding agent. Use the tools to inspect and edit files.
When asked to perform a coding task or fix an issue:
1. Inspect the codebase using bash commands.
2. Implement fixes using `write_file` or bash commands.
3. Test your changes to ensure everything passes before completing the goal."""

read_file_schema = {
    "name": "read_file",
    "description": "Read a text file and return its content. Whenever you are asked to read a file, use this tool. If the file does not exist, return an error message. Returns the content of the file as a string.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The path to the file to read."
            }
        },
        "required": ["path"]
    }
}

write_file_schema = {
    "name": "write_file",
    "description": "Write content to a text file. Whenever you are asked to write to a file, use this tool. If the file does not exist, create it. Returns a success message.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The path to the file to write."
            },
            "content": {
                "type": "string",
                "description": "The content to write to the file."
            }
        },
        "required": ["path", "content"]
    }
}

run_shell_schema = {
    "name": "run_shell",
    "description": "Run a shell command and return its output. Whenever you are asked to run a shell command, use this tool. Returns the output of the command as a string.",
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to run."
            }
        },
        "required": ["command"]
    }
}

TOOLS = [read_file_schema, write_file_schema, run_shell_schema]


def run_tool(tool_name: str, tool_input: dict) -> tuple[str, bool]:
    """Do what the model asked. Return (output_text, is_error) — never raise.

    Tool failures come back as text so the model can read them and react. Raising
    here would end the run instead, discarding everything done so far.

    Note `tool_input` is a plain dict (the provider hands back parsed JSON), so
    every field is read with ["key"], not attribute access.
    """
    try:
        if tool_name == "read_file":
            return open(tool_input["path"], "r").read(), False

        if tool_name == "write_file":
            content = tool_input["content"]
            open(tool_input["path"], "w").write(content)
            return f"Wrote {len(content)} chars to {tool_input['path']}", False

        if tool_name == "run_shell":
            # No check=True: a non-zero exit is exactly the case worth reporting,
            # and raising would throw away the output explaining why it failed.
            done = subprocess.run(
                tool_input["command"], shell=True, capture_output=True, text=True
            )
            output = (done.stdout + done.stderr) or "(no output)"
            return output, done.returncode != 0

        return f"Unknown tool: {tool_name}", True
    except Exception as e:
        return f"Error: {e}", True


def run_tool_calls(message: Message) -> list[dict]:
    """Execute every tool_use block in the message; return the result blocks."""
    tool_requests = [block for block in message.content if block.type == "tool_use"]

    tool_result_blocks = []
    for tool_request in tool_requests:
        print(f"  → {tool_request.name}({json.dumps(tool_request.input)[:80]})")
        output, is_error = run_tool(tool_request.name, tool_request.input)

        tool_result_blocks.append({
            "type": "tool_result",
            "tool_use_id": tool_request.id,
            "content": output,
            "is_error": is_error,
        })

    return tool_result_blocks


def text_from_message(message) -> str:
    return "\n".join(
        block.text for block in message.content if block.type == "text"
    )


# calls the LLM
def chat(messages, system=None, temperature=0.0, stop_sequences=None, tools=None):
    params = {
        "model": model,
        "max_tokens": 4096,
        "messages": messages,
        "temperature": temperature,
    }

    if stop_sequences:
        params["stop_sequences"] = stop_sequences

    if system:
        params["system"] = system

    if tools:
        params["tools"] = tools

    message = client.messages.create(**params)

    return message


def agent(messages: list) -> list:
    for _ in range(MAX_TURNS):
        res = chat(messages, system=SYSTEM_PROMPT, tools=TOOLS)
        messages.append({"role": "assistant", "content": res.content})

        text = text_from_message(res)
        if text:
            print(text)

        # Stop on content, not stop_reason — 03-production.md §3 step 14. A
        # response can carry tool calls while stop_reason says something else.
        tool_results = run_tool_calls(res)
        if not tool_results:
            break

        messages.append({"role": "user", "content": tool_results})
    else:
        print(f"\n[stopped: hit MAX_TURNS={MAX_TURNS}]")

    return messages


if __name__ == "__main__":
    messages = []
    print("Terminal Agent Ready! Type 'exit' to stop.\n")
    while True:
        usr_input = input("User: ")
        if usr_input.strip().lower() == "exit":
            break

        # Append, don't reassign — reassigning restarts the conversation every turn.
        messages.append({"role": "user", "content": usr_input})
        agent(messages)
