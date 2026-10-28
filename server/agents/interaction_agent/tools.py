"""Tool definitions for interaction agent."""

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Optional

from ...config import get_settings
from ...logging_config import logger
from ...services.approvals import ApprovalError, get_approval_store
from ...services.provenance import current_provenance, current_turn_id
from ...services.conversation import get_conversation_log
from ...services.execution import get_agent_registry, get_execution_agent_logs
from ...services.execution.card_store import cards_for, refresh_card
from ...services.execution.cards import MAIL_ID, AgentCard, extract_entities
from ...services.execution.registry import AgentRecord
from ...services.execution.selector import search, tokenize
from ...utils.clock import now_utc
from ..execution_agent.batch_manager import ExecutionBatchManager
from ..execution_agent.tools.gmail import execute_approved_action


@dataclass
class ToolResult:
    """Standardized payload returned by interaction-agent tools."""

    success: bool
    payload: Any = None
    user_message: Optional[str] = None
    recorded_reply: bool = False

# Tool schemas for OpenRouter
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "send_message_to_agent",
            "description": "Deliver instructions to a specific execution agent. Creates a new agent if the name doesn't exist in the roster, or reuses an existing one.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_name": {
                        "type": "string",
                        "description": "Human-readable agent name describing its purpose (e.g., 'Vercel Job Offer', 'Email to Sharanjeet'). This name will be used to identify and potentially reuse the agent."
                    },
                    "instructions": {"type": "string", "description": "Instructions for the agent to execute."},
                },
                "required": ["agent_name", "instructions"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_message_to_user",
            "description": "Deliver a natural-language response directly to the user. Use this for updates, confirmations, or any assistant response the user should see immediately.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Plain-text message that will be shown to the user and recorded in the conversation log.",
                    },
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_draft",
            "description": "Record an email draft so the user can review the exact text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Recipient email for the draft.",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject for the draft.",
                    },
                    "body": {
                        "type": "string",
                        "description": "Email body content (plain text).",
                    },
                },
                "required": ["to", "subject", "body"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "Wait silently when a message is already in conversation history to avoid duplicating responses. Adds a <wait> log entry that is not visible to the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Brief explanation of why waiting (e.g., 'Message already sent', 'Draft already created').",
                    },
                },
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
    },
]

CONFIRM_ACTION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "confirm_action",
        "description": "Send a pending email action (reply, forward, or draft send) that the user has just confirmed in their own message. Refused on any turn the user did not send.",
        "parameters": {
            "type": "object",
            "properties": {
                "action_id": {
                    "type": "string",
                    "description": "The action_id an agent reported for the pending send.",
                },
            },
            "required": ["action_id"],
            "additionalProperties": False,
        },
    },
}

# The routing tools the "shortlist" prompt variant describes: send_message_to_agent takes an id,
# the duplicate check can be overridden, and the model can ask the user or search the roster.
_SEND_BY_ID_SCHEMA = {
    "type": "function",
    "function": {
        "name": "send_message_to_agent",
        "description": "Deliver instructions to an execution agent. Pass agent_id to reuse an existing agent, or agent_name to start a new one; if similar agents exist the result lists them as possible_duplicates and nothing is sent.",
        "parameters": {
            "type": "object",
            "properties": {
                "instructions": {"type": "string", "description": "Instructions for the agent to execute."},
                "agent_id": {
                    "type": "string",
                    "description": "Id of an existing agent to reuse (e.g. 'a_0042'), from <active_agents> or search_agents.",
                },
                "agent_name": {
                    "type": "string",
                    "description": "Name for a new agent describing its matter (e.g. 'COI for Example Plumbing'). An existing agent's exact name reuses it.",
                },
                "confirm_new": {
                    "type": "boolean",
                    "description": "Create the new agent even though possible_duplicates were returned for this name.",
                },
            },
            "required": ["instructions"],
            "additionalProperties": False,
        },
    },
}

_CLARIFY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "clarify_agent_choice",
        "description": "Ask the user which matter they mean when more than one agent could fit. Sends the question to the user; no agent is contacted.",
        "parameters": {
            "type": "object",
            "properties": {
                "candidate_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ids of the agents that could fit.",
                },
                "question": {"type": "string", "description": "The question shown to the user."},
            },
            "required": ["candidate_ids", "question"],
            "additionalProperties": False,
        },
    },
}

_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_agents",
        "description": "Search all agents, including ones not shown in <active_agents> and archived ones, by name, email, id or topic.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Words to search for."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}

_EXECUTION_BATCH_MANAGER = ExecutionBatchManager()


def name_similarity(a: str, b: str) -> float:
    """Jaccard similarity of two names' word sets (lowercased, English stopwords dropped)."""
    left, right = set(tokenize(a)), set(tokenize(b))
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


# Thread, draft and message ids and policy numbers tie a request to a matter. Emails do not: a
# client's address appears on every one of its matters.
_DUPLICATE_ID_KINDS = (MAIL_ID, "policy")


def possible_duplicates(
    name: str, instructions: str, cards: list[AgentCard], threshold: float
) -> list[dict[str, str]]:
    """Existing agents that may already own the matter a new agent is being created for.

    A card matches when its name's similarity to ``name`` is at least ``threshold``, or when it holds
    a thread, draft, message or policy id that ``name`` or ``instructions`` mention. Cards sharing an
    id come first, then the closest names; at most five, each with the reason.
    """
    wanted = set(extract_entities(f"{name}\n{instructions}", kinds=_DUPLICATE_ID_KINDS))
    found = []
    for card in cards:
        similarity = name_similarity(name, card.name)
        shared = sorted(wanted.intersection(card.entities))
        reasons = []
        if similarity >= threshold:
            reasons.append(f"similar name ({similarity:.2f})")
        if shared:
            reasons.append("shares " + ", ".join(shared))
        if reasons:
            found.append((-len(shared), -similarity, card, "; ".join(reasons)))
    found.sort(key=lambda item: (item[0], item[1], item[2].agent_id))
    return [
        {"agent_id": card.agent_id, "name": card.name, "status": card.status, "why": why}
        for _, _, card, why in found[:5]
    ]


def _resolve_agent(
    agent_id: Optional[str], agent_name: Optional[str], confirm_new: bool, instructions: str
) -> tuple[Optional[AgentRecord], bool, Optional[ToolResult]]:
    """Find or create the target agent: ``(record, created, refusal)``."""
    registry = get_agent_registry()
    if agent_id:
        record = registry.get(agent_id.strip())
        if record is None:
            error = f"Unknown agent_id {agent_id!r}. Use an id from <active_agents> or search_agents, or pass agent_name to start a new agent."
            return None, False, ToolResult(success=False, payload={"error": error})
        return record, False, None
    if not agent_name or not agent_name.strip():
        error = "Pass agent_id to reuse an agent or agent_name to start a new one."
        return None, False, ToolResult(success=False, payload={"error": error})

    if confirm_new:
        # The model has seen the candidates and says this is a different matter, so it gets a new
        # agent even when the name is taken (the registry then suffixes the name).
        return registry.create(agent_name), True, None
    record = registry.find_by_name(agent_name)
    if record is not None:
        return record, False, None
    # Only the shortlist prompt explains confirm_new; under the upstream prompt a name always creates.
    if get_settings().prompt_variant == "shortlist":
        cards = cards_for(registry.list(status=None))
        similar = possible_duplicates(agent_name, instructions, cards, get_settings().duplicate_threshold)
        if similar:
            payload = {
                "status": "possible_duplicates",
                "possible_duplicates": similar,
                "message": "Nothing was sent. Reuse one of these by agent_id if it is the same matter, or call again with confirm_new=true.",
            }
            return None, False, ToolResult(success=False, payload=payload)
    record, created = registry.get_or_create(agent_name)
    return record, created, None


# Create or reuse execution agent and dispatch instructions asynchronously
def send_message_to_agent(
    instructions: str,
    agent_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    confirm_new: bool = False,
) -> ToolResult:
    """Send instructions to an execution agent, chosen by id or by name (with a duplicate check)."""
    record, is_new, refusal = _resolve_agent(agent_id, agent_name, confirm_new, instructions)
    if refusal is not None:
        return refusal
    if record.status == "archived":
        get_agent_registry().revive(record.id, now=now_utc())
    agent_name = record.name

    get_execution_agent_logs().record_request(agent_name, instructions)
    refresh_card(agent_name)

    action = "Created" if is_new else "Reused"
    logger.info(f"{action} agent: {agent_name}")

    async def _execute_async() -> None:
        try:
            result = await _EXECUTION_BATCH_MANAGER.execute_agent(agent_name, instructions)
            status = "SUCCESS" if result.success else "FAILED"
            logger.info(f"Agent '{agent_name}' completed: {status}")
        except Exception as exc:  # pragma: no cover - defensive
            logger.error(f"Agent '{agent_name}' failed: {str(exc)}")

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.error("No running event loop available for async execution")
        return ToolResult(success=False, payload={"error": "No event loop available"})

    loop.create_task(_execute_async())

    return ToolResult(
        success=True,
        payload={
            "status": "submitted",
            "agent_id": record.id,
            "agent_name": agent_name,
            "new_agent_created": is_new,
        },
    )


# Ask the user to choose between candidate agents; nothing is dispatched
def clarify_agent_choice(candidate_ids: list[str], question: str) -> ToolResult:
    """Send the user a question about which agent they mean."""
    registry = get_agent_registry()
    unknown = [agent_id for agent_id in candidate_ids if registry.get(str(agent_id)) is None]
    if not candidate_ids or unknown:
        return ToolResult(success=False, payload={"error": f"Unknown candidate_ids: {unknown or 'none given'}"})
    get_conversation_log().record_reply(question)
    return ToolResult(
        success=True,
        payload={"status": "asked", "candidate_ids": list(candidate_ids)},
        user_message=question,
        recorded_reply=True,
    )


# Search every agent, archived ones included, by name, email, id or topic
def search_agents(query: str) -> ToolResult:
    """Return the best-matching agents' cards."""
    now = now_utc()
    cards = cards_for(get_agent_registry().list(status=None))
    results = [
        {
            "agent_id": hit.card.agent_id,
            "name": hit.card.name,
            "status": hit.card.status,
            "last_active_at": hit.card.last_active_at,
            "purpose": hit.card.purpose,
            "entities": list(hit.card.entities),
        }
        for hit in search(query, cards, now=now)
    ]
    return ToolResult(success=True, payload={"query": query, "agents": results})


# Send immediate message to user and record in conversation history
def send_message_to_user(message: str) -> ToolResult:
    """Record a user-visible reply in the conversation log."""
    log = get_conversation_log()
    log.record_reply(message)

    return ToolResult(
        success=True,
        payload={"status": "delivered"},
        user_message=message,
        recorded_reply=True,
    )


# Format and record email draft for user review
def send_draft(
    to: str,
    subject: str,
    body: str,
) -> ToolResult:
    """Record a draft update in the conversation log for the interaction agent."""
    log = get_conversation_log()

    message = f"To: {to}\nSubject: {subject}\n\n{body}"

    log.record_reply(message)
    logger.info(f"Draft recorded for: {to}")

    return ToolResult(
        success=True,
        payload={
            "status": "draft_recorded",
            "to": to,
            "subject": subject,
        },
        recorded_reply=True,
    )


# Approve a pending outbound action on the user's word and send it once
def confirm_action(action_id: str) -> ToolResult:
    """Approve a pending action if this turn came from the user, then execute it."""
    try:
        action = get_approval_store().approve(action_id, current_provenance(), current_turn_id())
        result = execute_approved_action(action.id)
    except ApprovalError as exc:
        logger.warning("action not approved", extra={"action_id": action_id, "reason": str(exc)})
        return ToolResult(success=False, payload={"error": str(exc), "action_id": action_id})

    return ToolResult(
        success=True,
        payload={"status": "sent", "action_id": action.id, "kind": action.kind, "result": result},
    )


# Record silent wait state to avoid duplicate responses
def wait(reason: str) -> ToolResult:
    """Wait silently and add a wait log entry that is not visible to the user."""
    log = get_conversation_log()
    
    # Record a dedicated wait entry so the UI knows to ignore it
    log.record_wait(reason)
    

    return ToolResult(
        success=True,
        payload={
            "status": "waiting",
            "reason": reason,
        },
        recorded_reply=True,
    )


# Return the tool schemas for the configured prompt variant and roster mode
def get_tool_schemas():
    """Return OpenAI-compatible tool schemas.

    The "original" variant gets exactly the upstream four (TOOL_SCHEMAS), so the baseline arm
    measures upstream; it has no confirm_action, so a pending send cannot be approved there. The
    "shortlist" variant gets id-based routing, clarify_agent_choice, search_agents when the roster
    is shortlisted, and confirm_action.
    """
    settings = get_settings()
    if settings.prompt_variant == "original":
        return TOOL_SCHEMAS
    routing = [_SEND_BY_ID_SCHEMA, _CLARIFY_SCHEMA]
    if settings.roster_mode == "shortlist":
        routing.append(_SEARCH_SCHEMA)
    return [*routing, *TOOL_SCHEMAS[1:], CONFIRM_ACTION_SCHEMA]


# Route tool calls to appropriate handlers with argument validation and error handling
def handle_tool_call(name: str, arguments: Any) -> ToolResult:
    """Handle tool calls from interaction agent."""
    try:
        if isinstance(arguments, str):
            args = json.loads(arguments) if arguments.strip() else {}
        elif isinstance(arguments, dict):
            args = arguments
        else:
            return ToolResult(success=False, payload={"error": "Invalid arguments format"})

        if name == "confirm_action":
            return confirm_action(**args)
        if name == "send_message_to_agent":
            return send_message_to_agent(**args)
        if name == "clarify_agent_choice":
            return clarify_agent_choice(**args)
        if name == "search_agents":
            return search_agents(**args)
        if name == "send_message_to_user":
            return send_message_to_user(**args)
        if name == "send_draft":
            return send_draft(**args)
        if name == "wait":
            return wait(**args)

        logger.warning("unexpected tool", extra={"tool": name})
        return ToolResult(success=False, payload={"error": f"Unknown tool: {name}"})
    except json.JSONDecodeError:
        return ToolResult(success=False, payload={"error": "Invalid JSON"})
    except TypeError as exc:
        return ToolResult(success=False, payload={"error": f"Missing required arguments: {exc}"})
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("tool call failed", extra={"tool": name, "error": str(exc)})
        return ToolResult(success=False, payload={"error": "Failed to execute"})
