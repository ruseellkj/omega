import json, os, subprocess, sys
from anthropic import Anthropic
from anthropic.types import Message, ToolParam
from dotenv import load_dotenv

# override=True so this project's .env wins over any ANTHROPIC_API_KEY the shell
# already exports. Without it dotenv silently keeps the inherited value and .env
# is never read.
load_dotenv(override=True)

if not os.environ.get("ANTHROPIC_API_KEY"):
    sys.exit("ANTHROPIC_API_KEY not set — add it to .env (see .env.sample)")

client = Anthropic()
model = "claude-sonnet-5"

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

def run_tool(name: str, input: ToolParam) -> str:
    try:
        if name == "read_file":
            return open(input.path, "r").read()
        if name == "write_file":
            open(input.path, "w").write(input.content)
            return f"Wrote {len(input.content)} chars to {input.path}"
        if name == "run_shell":
            done = subprocess.run(input.command, shell=True, capture_output=True, text=True, check=True).stdout
            return (done.stdout + done.stderr) or "(no output)"
        return f"Unknown tool: {name}"
    except Exception as e:
        return f"Error: {e}"

def run_tool_calls(message: str) -> None:
    tool_requests = [block for block in message.content if block.type == "tool_use"]
    tool_result_blocks = []

    for tool_request in tool_requests:
        try:
            tool_output = run_tool(tool_request.name, tool_request.input)
            tool_result_block = {
                "type": "tool_result",
                "tool_use_id": tool_request.id,
                "content": json.dumps(tool_output),
                "is_error": False,
            }
        except Exception as e:
            tool_result_block = {
                "type": "tool_result",
                "tool_use_id": tool_request.id,
                "content": f"Error: {e}",
                "is_error": True,
            }
        tool_result_blocks.append(tool_result_block)
    return tool_result_blocks


def text_from_message(message):
    return "\n".join(
        block.text for block in message.content if block.type == "text"
    )


# calls the LLM
def chat(messages, system=None, temperature=0.7, stop_sequences=[], tools=None):
    params = {
        "model": model,
        "max_tokens": 4096,
        "messages": messages,
        "temperature": temperature,
        "stop_sequences": stop_sequences,
    }

    if system:
        params["system"] = system

    if tools:
        params["tools"] = tools

    message = client.messages.create(**params)

    return message


def agent(messages: str) -> None:
    while True:
        res = chat(messages, system=SYSTEM_PROMPT, tools=TOOLS)
        messages.append({"role": "assistant", "content": res.content})
        text_from_message(res)
        if res.stop_reason != "tool_use":
            break

        tool_results = run_tool_calls(res)
        messages.append({"role": "user", "content": tool_results})

    return messages


if __name__ == "__main__":
    messages = []
    print("Terminal Agent Ready! Type 'exit' to stop.\n")
    while True:
        usr_input = input("User: ")
        if usr_input.strip().lower() == "exit":
            break
        
        messages = [{"role": "user", "content": usr_input}] 
        agent(messages)