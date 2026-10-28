"""Execution Agent implementation."""

from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple

from ...config import execution_memory
from ...services.execution import get_execution_agent_logs
from ...services.execution.log_store import render_entries
from ...services.execution.selector import BM25, tokenize
from ...logging_config import logger

# OPENPOKE_EXECUTION_MEMORY=retrieved: the latest entries plus the older ones most relevant to the request.
RECENT_ENTRIES = 3
RETRIEVED_ENTRIES = 8


def retrieve_entries(entries: List[Tuple[str, str, str]], instructions: str) -> List[Tuple[str, str, str]]:
    """The last RECENT_ENTRIES entries plus the RETRIEVED_ENTRIES older ones scoring best by BM25
    against ``instructions``, in log order. An entry sharing no word with the request is never picked."""
    if len(entries) <= RECENT_ENTRIES + RETRIEVED_ENTRIES:
        return list(entries)
    older = len(entries) - RECENT_ENTRIES
    bm25 = BM25([tokenize(payload) for _, _, payload in entries[:older]])
    query = tokenize(instructions)
    scored = sorted(((bm25.score(query, i), i) for i in range(older)), reverse=True)
    best = {i for score, i in scored[:RETRIEVED_ENTRIES] if score > 0}
    return [entries[i] for i in sorted(best | set(range(older, len(entries))))]


# Load system prompt template from file
_prompt_path = Path(__file__).parent / "system_prompt.md"
if _prompt_path.exists():
    SYSTEM_PROMPT_TEMPLATE = _prompt_path.read_text(encoding="utf-8").strip()
else:
    # Placeholder template - you'll replace this with actual instructions
    SYSTEM_PROMPT_TEMPLATE = """You are an execution agent responsible for completing specific tasks using available tools.

Agent Name: {agent_name}
Purpose: {agent_purpose}

Instructions:
[TO BE FILLED IN BY USER]

You have access to Gmail tools to help complete your tasks. When given instructions:
1. Analyze what needs to be done
2. Use the appropriate tools to complete the task
3. Provide clear status updates on your actions

Be thorough, accurate, and efficient in your execution."""


class ExecutionAgent:
    """Manages state and history for an execution agent."""

    # Initialize execution agent with name, conversation limits, and log store access
    def __init__(
        self,
        name: str,
        conversation_limit: Optional[int] = None
    ):
        """
        Initialize an execution agent.

        Args:
            name: Human-readable agent name (e.g., 'conversation with keith')
            conversation_limit: Optional limit on past conversations to include (None = all)
        """
        self.name = name
        self.conversation_limit = conversation_limit
        self._log_store = get_execution_agent_logs()

    # Generate system prompt template with agent name and purpose derived from name
    def build_system_prompt(self) -> str:
        """Build the system prompt for this agent."""
        agent_purpose = f"Handle tasks related to: {self.name}"

        return SYSTEM_PROMPT_TEMPLATE.format(
            agent_name=self.name,
            agent_purpose=agent_purpose
        )

    # Combine base system prompt with conversation history, applying conversation limits
    def build_system_prompt_with_history(self, instructions: str = "") -> str:
        """
        Build system prompt including agent history.

        Args:
            instructions: The request being handled; read only under OPENPOKE_EXECUTION_MEMORY=retrieved,
                which replaces conversation_limit with retrieve_entries.

        Returns:
            System prompt with embedded history transcript
        """
        base_prompt = self.build_system_prompt()

        if execution_memory() == "retrieved":
            entries = list(self._log_store.iter_entries(self.name))
            if not entries:
                return base_prompt
            kept = retrieve_entries(entries, instructions)
            note = f"{len(kept)} of {len(entries)} entries: the latest and those most relevant to this request."
            return f"{base_prompt}\n\n# Execution History\n\n{note}\n{render_entries(kept)}"

        # Load history transcript
        transcript = self._log_store.load_transcript(self.name)

        if transcript:
            # Apply conversation limit if needed
            if self.conversation_limit and self.conversation_limit > 0:
                # Parse entries and limit them
                lines = transcript.split('\n')
                request_count = sum(1 for line in lines if '<agent_request' in line)

                if request_count > self.conversation_limit:
                    # Find where to cut
                    kept_requests = 0
                    cutoff_index = len(lines)
                    for i in range(len(lines) - 1, -1, -1):
                        if '<agent_request' in lines[i]:
                            kept_requests += 1
                            if kept_requests == self.conversation_limit:
                                cutoff_index = i
                                break
                    transcript = '\n'.join(lines[cutoff_index:])

            return f"{base_prompt}\n\n# Execution History\n\n{transcript}"

        return base_prompt

    # Format current instruction as user message for LLM consumption
    def build_messages_for_llm(self, current_instruction: str) -> List[Dict[str, str]]:
        """
        Build message array for LLM call.

        Args:
            current_instruction: Current instruction from interaction agent

        Returns:
            List of messages in OpenRouter format
        """
        return [
            {"role": "user", "content": current_instruction}
        ]

    # Log the agent's final response to the execution log store
    def record_response(self, response: str) -> None:
        """Record agent's response to the log."""
        self._log_store.record_agent_response(self.name, response)

    # Log tool invocation and results with truncated content for readability
    def record_tool_execution(self, tool_name: str, arguments: str, result: str) -> None:
        """Record tool execution details."""
        self._log_store.record_action(self.name, f"Calling {tool_name} with: {arguments[:200]}")
        # Record the tool response
        self._log_store.record_tool_response(self.name, tool_name, result[:500])
