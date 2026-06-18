"""Claude Code CLI wrapper and JSON extraction utilities."""
import json
import re
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def run_claude_code(prompt, project_id=None):
    """Call Claude Code CLI and return parsed response."""
    try:
        cmd = ["claude", "-p", prompt, "--output-format", "json"]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(BASE_DIR),
        )
        stdout = result.stdout.strip()
        if not stdout:
            return {"text": "No response from Claude Code.", "error": True}

        try:
            parsed = json.loads(stdout)
            if isinstance(parsed, dict) and "result" in parsed:
                return {"text": parsed["result"], "error": False}
            if isinstance(parsed, list):
                texts = []
                for item in parsed:
                    if isinstance(item, dict) and item.get("type") == "result":
                        texts.append(item.get("result", ""))
                    elif isinstance(item, dict) and "content" in item:
                        for block in item.get("content", []):
                            if isinstance(block, dict) and block.get("type") == "text":
                                texts.append(block.get("text", ""))
                return {"text": "\n".join(texts) if texts else stdout, "error": False}
            return {"text": str(parsed), "error": False}
        except json.JSONDecodeError:
            return {"text": stdout, "error": False}
    except subprocess.TimeoutExpired:
        return {"text": "Claude Code timed out after 300 seconds.", "error": True}
    except FileNotFoundError:
        return {"text": "Claude Code CLI not found. Make sure 'claude' is installed and in PATH.", "error": True}
    except Exception as e:
        return {"text": f"Error calling Claude Code: {str(e)}", "error": True}


def extract_json_from_text(text):
    """Extract JSON blocks from Claude Code response text."""
    results = {}
    patterns = {
        "form_fields": r"```(?:json)?\s*//\s*form_fields\s*\n([\s\S]*?)```",
        "sub_agents": r"```(?:json)?\s*//\s*sub_agents\s*\n([\s\S]*?)```",
        "tools_needed": r"```(?:json)?\s*//\s*tools_needed\s*\n([\s\S]*?)```",
        "mcp_servers": r"```(?:json)?\s*//\s*mcp_servers\s*\n([\s\S]*?)```",
        "dashboard_metrics": r"```(?:json)?\s*//\s*dashboard_metrics\s*\n([\s\S]*?)```",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        if match:
            try:
                results[key] = json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

    if not results:
        json_match = re.search(r"```(?:json)?\s*\n(\{[\s\S]*?\})\s*```", text)
        if json_match:
            try:
                parsed = json.loads(json_match.group(1))
                results.update(parsed)
            except json.JSONDecodeError:
                pass

    return results
