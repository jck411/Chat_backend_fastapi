# LLM Tool Decision Guide

**What the LLM Actually Sees When Choosing Tools**

This document shows **exactly** what information the LLM uses to decide which tools to call, listed in priority order from most to least influential.

---

## How Tools Are Registered and Exposed

### The MCP → OpenAI Tool Chain

Your tools go through this transformation pipeline before the LLM sees them:

1. **MCP Server** - Tools defined with `@mcp.tool()` decorator
2. **MCP Client** (`mcp_client.py`) - Converts to OpenAI format via `get_openai_tools()`
3. **MCP Registry** (`mcp_registry.py`) - Aggregates tools from multiple servers, applies prefixes
4. **Orchestrator** (`orchestrator.py`) - Injects tools into conversation context
5. **OpenRouter/OpenAI API** - Sends tools array to LLM

### What the LLM Receives (OpenAI Tool Format)

Each tool is sent to the LLM as a JSON object:

```json
{
  "type": "function",
  "function": {
    "name": "search_all_tasks",
    "description": "Search Google Tasks across every list to learn what the user plans...",
    "parameters": {
      "type": "object",
      "properties": {
        "query": {
          "type": "string",
          "description": "Search query string...",
          "default": ""
        },
        "user_email": {
          "type": "string",
          "default": "jck411@gmail.com"
        }
      },
      "required": ["query"]
    }
  }
}
```

**This is the EXACT data structure the LLM sees.** Nothing more, nothing less.

### Key Transformation: MCP → OpenAI

From `mcp_client.py`:

```python
def get_openai_tools(self) -> list[dict[str, Any]]:
    """Return tools formatted for OpenAI/OpenRouter tool definitions."""

    formatted: list[dict[str, Any]] = []
    for tool in self._tools:
        description = tool.description or tool.title or ""
        entry: dict[str, Any] = {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": description,  # ← YOUR DOCSTRING GOES HERE
                "parameters": tool.inputSchema or {"type": "object", "properties": {}},
            },
        }
        formatted.append(entry)
    return formatted
```

**Critical insight:** The `description` field comes from your tool's docstring. This becomes the LLM's primary decision signal.

### Registry Enhancement: Server ID Prefix

From `mcp_registry.py`, the registry adds server context:

```python
enriched_function["description"] = f"[{config.id}] {desc}"
```

So if your tool description is:
```
"Search Google Tasks across every list..."
```

The LLM actually sees:
```
"[custom-calendar] Search Google Tasks across every list..."
```

This helps the LLM understand which server/domain a tool belongs to.

### Context Tags (Advanced Feature)

Tools can be tagged with contexts in `mcp_servers.json`:

```json
{
  "id": "custom-calendar",
  "contexts": ["scheduling", "tasks", "personal"],
  "tool_overrides": {
    "search_all_tasks": {
      "contexts": ["preferences", "recommendations"]
    }
  }
}
```

The orchestrator can filter tools by context, but **the LLM doesn't directly see these tags** - they affect which tools are included in the request.

---

## Decision-Making Hierarchy

Based on the OpenAI tool format, the LLM evaluates tools in this priority order:

### 🔴 **PRIORITY 1: Tool Description** (50% Weight - HIGHEST)
The `function.description` field in the OpenAI tool JSON. This comes from your Python docstring.

**Example:**
```python
@mcp.tool("search_all_tasks")
async def search_all_tasks(...):
    """Search Google Tasks across every list to learn what the user plans, wants, or needs.
    Call this whenever the user asks about what they have to do, want to read/watch/eat/buy,
    or before offering personal suggestions—questions like "what books do I want to read?"
    should trigger this tool."""
```

**What LLM sees:** The entire docstring as the `description` field, prefixed with `[custom-calendar]`.

### 🟠 **PRIORITY 2: Tool Name** (25% Weight)
The `function.name` field. Names with clear verbs (search, get, create) are strong signals.

**Example:** `search_all_tasks` clearly indicates a search action on tasks.

**Registry impact:** May be prefixed as `custom-calendar__search_all_tasks` if name conflicts exist.

### 🟡 **PRIORITY 3: Parameter Schema** (15% Weight)
The `function.parameters` object (JSON Schema). Tells the LLM what inputs are expected.

**Example:**
```json
"parameters": {
  "type": "object",
  "properties": {
    "query": {"type": "string", "default": ""},
    "max_results": {"type": "integer", "default": 25}
  },
  "required": ["query"]
}
```

**Critical fields:**
- `properties` - Available parameters
- `required` - Which parameters MUST be provided
- `default` - Optional parameters with defaults
- Parameter `type` - Constrains LLM's choices (string, integer, boolean, array, etc.)

### 🟢 **PRIORITY 4: Parameter Descriptions** (7% Weight)
Individual parameter descriptions in the JSON Schema's `properties`.

**Example:**
```json
"query": {
  "type": "string",
  "description": "Search keywords (empty string returns general overview)",
  "default": ""
}
```

### 🔵 **PRIORITY 5: Default Values** (3% Weight)
Defaults in the schema signal what's optional and what's typical.

**Example:** `"default": "jck411@gmail.com"` tells the LLM this user is standard.

---

## Critical Tools with High-Priority Descriptions

These tools have descriptions that explicitly guide LLM behavior:

### 🔴 **search_all_tasks** - HIGHEST PRIORITY CONTEXT TOOL

```python
@mcp.tool("search_all_tasks")
async def search_all_tasks(...):
    """Search Google Tasks across every list to learn what the user plans, wants, or needs.
    Call this whenever the user asks about what they have to do, want to read/watch/eat/buy,
    or before offering personal suggestions—questions like "what books do I want to read?"
    should trigger this tool. Prefer short keyword queries copied from the user's request
    (for example, "books"). If you do not have a specific keyword, pass an empty string
    and this tool will return a general overview of recent tasks.
    """
```

**Why this matters:** The description contains explicit trigger conditions:
- "whenever the user asks about what they have to do"
- "before offering personal suggestions"
- "questions like 'what books do I want to read?' should trigger this tool"

**LLM sees:** 158 words of direct instruction about when to call this tool.

---

### 🔴 **current_time** - Critical for Temporal Context

```python
@mcp.tool(
    "current_time",
    description=(
        "Retrieve the current moment with precise Unix timestamps plus UTC and Eastern Time "
        "(ET/EDT) ISO formats. Use this whenever the conversation needs an up-to-date clock "
        "reference or time zone comparison."
    ),
)
async def current_time(...):
    """Return the current time with UTC and Eastern Time representations."""
```

**Why this matters:** Two descriptions (decorator + docstring). The decorator says "Use this whenever..." which is directive language.

---

### 🔴 **chat_history** - Conversation Memory

```python
@mcp.tool(
    "chat_history",
    description=(
        "Return stored chat messages for a session, including ISO timestamps. "
        "Use this tool whenever you need precise timing for earlier turns. "
        "Provide a session_id (automatically supplied by the orchestrator) to retrieve "
        "the most recent messages."
    ),
)
async def chat_history(...):
    """Retrieve recent chat messages plus timestamps for an existing session."""
```

**Why this matters:** Explains auto-injection of `session_id` and when to use timestamps.

---

### 🟠 **calendar_create_task** - Critical Warning

```python
@mcp.tool("calendar_create_task")
async def create_task(...):
    """
    Create a new Google Task.

    IMPORTANT: When the user asks to "schedule a task for [date/time]", you MUST
    include the 'due' parameter to actually schedule it. Without 'due', the task
    will be created unscheduled. Use 'due="YYYY-MM-DD"' for date-only scheduling
    or 'due="YYYY-MM-DDTHH:MM:SSZ"' for specific times.

    When scheduling EXISTING tasks, use calendar_update_task instead of creating
    duplicates.
    """
```

**Why this matters:** "MUST" in ALL CAPS is the strongest possible directive. Explains a common mistake.

---

### 🟠 **calendar_get_events** - Smart Aggregation

```python
@mcp.tool("calendar_get_events")
async def get_events(...):
    """
    Retrieve events across the user's Google calendars.

    With no ``calendar_id`` (or when using phrases such as "my schedule") the
    search spans the preconfigured household calendars.
    Provide a specific ID or friendly name (for example "Family Calendar" or
    "Dad Work Schedule") to narrow the query to a single calendar.
    """
```

**Why this matters:** Explains natural language → behavior mapping ("my schedule" → aggregate query).

---

## Tool Names: Semantic Signals

Tool names give strong hints about behavior:

### ✅ Strong Semantic Names (Clear Intent)
- `search_gmail_messages` - Search action, Gmail target
- `get_gmail_message_content` - Retrieve action, specific item
- `create_event` - Action verb first
- `list_upload_paths` - Listing action
- `extract_document` - Transform action

### ⚠️ Weaker Names (Need Strong Descriptions)
- `test_echo` - Purpose unclear without description
- `manage_gmail_label` - "Manage" is vague (create? update? delete?)

---

## Parameter Names: Guidance Clues

Parameter names the LLM relies on heavily:

### 🔍 Search/Query Parameters
- `query` - Text search string
- `search_query` - Explicit search
- `pattern` - Pattern matching

### 🆔 Identifier Parameters
- `message_id` - Gmail message identifier
- `file_id` - Drive file identifier
- `attachment_id` - Saved attachment
- `event_id` - Calendar event
- `task_id` - Task identifier
- `session_id` - Chat session

### 📅 Time Parameters
- `time_min` / `time_max` - Time ranges
- `due` - Task due date
- `start_time` / `end_time` - Event timing

### ⚙️ Behavior Modifiers
- `force_ocr` - Boolean flag
- `detailed` - Verbosity control
- `max_results` - Result limiting
- `newest_first` - Sort order

---

## Type Hints: Constraint Signals

Type hints constrain the LLM's choices:

### Literal Types (Strongest Constraints)
```python
body_format: Literal["plain", "html"] = "plain"
# LLM knows ONLY these two values are valid

action: Literal["create", "update", "delete"]
# LLM must choose one of exactly three actions

format: Literal["iso", "unix"] = "iso"
# Clear format options
```

### Optional vs Required
```python
message_id: str  # REQUIRED - LLM must provide
query: Optional[str] = None  # OPTIONAL - LLM can omit
subject: str = ""  # HAS DEFAULT - LLM can use or override
```

### List Types
```python
message_ids: List[str]  # LLM knows to pass array
attendees: Optional[List[str]] = None  # Optional array
```

---

## Default Values: Behavior Hints

Defaults tell the LLM what's "normal":

### User Email Default
```python
user_email: str = DEFAULT_USER_EMAIL  # "jck411@gmail.com"
```
**Signal:** This user is the primary target. Don't ask for email unless needed.

### Pagination Defaults
```python
max_results: int = 25
page_size: int = 10
```
**Signal:** Reasonable result limits. Can override for "show me everything" requests.

### Boolean Flags
```python
force_ocr: bool = False
show_completed: bool = False
detailed: bool = False
```
**Signal:** Conservative defaults. LLM should enable explicitly when needed.

---

## Common Decision Patterns

### Pattern 1: Context Before Action
**LLM sees tools in this order (by description priority):**

1. `search_all_tasks` - "Call this whenever..."
2. `calendar_get_events` - "Retrieve events" (with `include_tasks=True` for combined view)
3. `calendar_create_event` - "Create event"

**Decision:** For "recommend a book", LLM calls #1 FIRST to get user's reading list.

---

### Pattern 2: Specific → General Fallback

**Gmail tools:**
- `get_gmail_message_content` - Get one message
- `get_gmail_messages_content_batch` - Get multiple
- `search_gmail_messages` - Find messages

**Decision:** If user says "show me the latest email", LLM searches first, then fetches content.

---

### Pattern 3: ID vs. Search

**Drive tools offer two paths:**
- `gdrive_list_folder(folder_id="xyz")` - Direct access
- `gdrive_list_folder(folder_name="Reports")` - Name resolution
- `gdrive_list_folder(folder_path="Reports/2024")` - Path traversal

**Decision:** Based on parameter names. If user says "Reports folder", LLM uses `folder_name`.

---

### Pattern 4: Save → Extract Workflow

**Attachment tools:**
1. `download_gmail_attachment` - Saves to storage, returns `attachment_id`
2. `extract_saved_attachment` - Uses `attachment_id` to get text

**Decision:** Parameter type (`attachment_id: str`) in #2 signals it expects output from #1.

---

## Explicit Trigger Words in Descriptions

These phrases in tool descriptions directly influence LLM decisions:

### Command Language
- ✅ "**Always call** this before..." → Strong imperative
- ✅ "**Use this whenever**..." → Clear trigger condition
- ✅ "**MUST include**..." → Non-negotiable requirement
- ✅ "Call this **whenever the user asks** about..." → Pattern matching
- ✅ "**IMPORTANT:**" → Attention grabber

### Conditional Language
- ⚠️ "With no `calendar_id`..." → Explains optional behavior
- ⚠️ "When not provided..." → Fallback explanation
- ⚠️ "If..." → Conditional logic

### Examples in Descriptions
```python
"""...questions like "what books do I want to read?" should trigger this tool."""
```
**Effect:** LLM pattern-matches user input against these examples.

---

## Anti-Patterns: What Confuses the LLM

### ❌ Vague Tool Names
```python
@mcp.tool("process_data")  # What data? How processed?
```

### ❌ Generic Descriptions
```python
"""Handle user requests."""  # Too broad
```

### ❌ Missing Trigger Conditions
```python
"""Get some information."""  # When? What information?
```

### ❌ Inconsistent Naming
```python
@mcp.tool("gmail_search")  # vs
@mcp.tool("search_gmail_messages")  # Better - verb first
```

---

## Real-World Example: How LLM Chooses

**User input:** "What do I want to read?"

**LLM reasoning (based on tool info):**

1. **Scans tool names** for "read", "want", "book"
   - No exact match

2. **Checks descriptions** for trigger phrases:
   - ✅ `search_all_tasks`: "whenever the user asks about what they have to do, want to read/watch/eat/buy"
   - ✅ Contains example: "what books do I want to read?"
   - ✅ Says "should trigger this tool"

3. **Examines parameters:**
   - `query: str = ""`
   - Description says "prefer short keyword queries"
   - Empty string gets "general overview"

4. **Decision:** Call `search_all_tasks(query="read")`

**Result:** User gets their reading list from Google Tasks.

---

## Priority-Ranked Tool List

Tools ranked by how strongly their descriptions guide LLM decisions:

### 🔴 **Tier 1: Explicit Guidance (LLM almost always gets it right)**
1. `search_all_tasks` - "Call this whenever user asks..." (use empty query for overview)
2. `current_time` - "Use this whenever conversation needs up-to-date clock"
3. `chat_history` - "Use this tool whenever you need precise timing"
4. `calendar_create_task` - "IMPORTANT: MUST include 'due' parameter"
5. `calendar_get_events` - Use `include_tasks=True` for combined calendar + tasks view

### 🟠 **Tier 2: Strong Descriptions (Clear but not imperative)**
6. `calendar_get_events` - "With no calendar_id... search spans..."
7. `read_gmail_attachment_text` - Explains selection priority
8. `extract_document` - "Extract text and data from local files or HTTP(S) URLs"
9. `list_upload_paths` - "List files under configured uploads directory"
10. `gdrive_get_file_content` - "Retrieve and extract text content"

### 🟡 **Tier 3: Descriptive but Generic**
11. `search_gmail_messages` - "Search Gmail messages using Gmail search syntax"
12. `get_gmail_message_content` - "Retrieve full content of specific message"
13. `calendar_list_calendars` - "List calendars the user has access to"
14. `gdrive_search_files` - "Search for files in Google Drive"
15. `send_gmail_message` - "Send a Gmail message"

### 🟢 **Tier 4: Basic Descriptions (Rely on tool name + params)**
16. `calculator_evaluate` - "Perform a simple arithmetic operation"
17. `gdrive_create_file` - "Create a new file in Google Drive"
18. `gdrive_rename_file` - "Rename a Drive file"
19. Most CRUD operations (create/update/delete)

### 🔵 **Tier 5: Minimal Guidance (Name + parameters are primary signal)**
20. Auth status/URL generation tools - Self-explanatory names
21. List/get operations without special behavior
22. Simple management operations

---

## Recommendations for Influencing LLM Behavior

### ✅ DO: Write Directive Descriptions
```python
"""Always call this before [action]. Use this whenever [condition]."""
```

### ✅ DO: Include Example Queries
```python
"""...questions like "what books do I want to read?" should trigger this tool."""
```

### ✅ DO: Explain Special Behavior
```python
"""With no calendar_id, searches all configured calendars."""
```

### ✅ DO: Use Strong Language for Critical Points
```python
"""IMPORTANT: You MUST include the 'due' parameter..."""
```

### ✅ DO: Explain Parameter Relationships
```python
"""Provide attachment_id from download_gmail_attachment output."""
```

### ❌ DON'T: Write Generic Descriptions
```python
"""Handles data."""  # Too vague
```

### ❌ DON'T: Rely Only on Parameter Docs
```python
# Docstring is weak, param docs alone won't help
```

### ❌ DON'T: Use Jargon Without Context
```python
"""Performs OAuth2 PKCE flow."""  # What does this mean to LLM?
# Better: "Generate authorization URL for user to grant access"
```

---

## Summary: What Drives Tool Selection

**In priority order:**

1. **Tool description (docstring)** - 50% weight
   - Explicit triggers: "Call this whenever..."
   - Examples: "questions like..."
   - Imperatives: "MUST", "Always", "IMPORTANT"

2. **Tool name** - 25% weight
   - Action verbs: search, get, create, list
   - Clear targets: gmail, calendar, drive
   - Pattern: `action_target_object`

3. **Parameter names** - 15% weight
   - Semantic hints: query, message_id, file_path
   - Required vs optional signals intent

4. **Type constraints** - 7% weight
   - Literal types constrain choices
   - Optional types suggest when to omit

5. **Default values** - 3% weight
   - Show normal/expected values
   - Signal what's safe to omit

---

## Quick Reference: High-Impact Description Patterns

| Pattern | Example | Effect |
|---------|---------|--------|
| **"Always..."** | "Always call this before recommendations" | Strong imperative |
| **"Use this whenever..."** | "Use this whenever conversation needs clock reference" | Clear trigger |
| **"MUST"** | "You MUST include 'due' parameter" | Non-negotiable |
| **"Call this whenever [user asks]..."** | "Call this whenever user asks what they want to read" | Pattern match |
| **"Questions like '...'"** | "Questions like 'what books do I want to read?'" | Example triggers |
| **"With no [param]..."** | "With no calendar_id, searches all calendars" | Behavior explanation |
| **"IMPORTANT:"** | "IMPORTANT: Without 'due', task is unscheduled" | Attention flag |

---

## Backend Validation and Special Handling

**Important:** The backend has additional logic that runs AFTER the LLM makes tool decisions.

### Calendar Task Validation (from `mcp_client.py`)

When `calendar_create_task` is called, the client performs intelligent validation:

```python
async def call_tool(self, name: str, arguments: dict[str, Any] | None = None):
    # Validate calendar_create_task calls for common mistakes
    if name == "calendar_create_task" and arguments:
        title = str(arguments.get("title", "")).lower()
        notes = str(arguments.get("notes", "")).lower()
        has_due = arguments.get("due") is not None

        # Check if the title or notes contain scheduling keywords
        scheduling_keywords = [
            "today", "tomorrow", "schedule", "due", "deadline",
            "monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday", "next week", "this week",
        ]

        has_scheduling_keyword = any(
            keyword in title or keyword in notes
            for keyword in scheduling_keywords
        )

        if has_scheduling_keyword and not has_due:
            logger.warning(
                "Creating task with scheduling keywords but missing 'due' parameter. "
                "Task will be created unscheduled."
            )
```

**What this means:**
- Even if the LLM forgets to include `due` parameter, the backend logs a warning
- This doesn't fix the mistake, but it provides debugging insight
- **The tool description's "IMPORTANT: MUST include 'due'" is still the primary mechanism**

### System Prompt Enhancement (from `orchestrator.py`)

The orchestrator automatically prepends time context to EVERY conversation:

```python
def _build_enhanced_system_prompt(base_prompt: str | None) -> str:
    """Prepend the current time context to the configured system prompt."""

    snapshot = create_time_snapshot()
    context_lines = [
        "# Current Date & Time Context",
        f"- Today's date: {snapshot.date.isoformat()} ({snapshot.now_local.strftime('%A')})",
        f"- Current time: {snapshot.format_time()}",
        f"- Timezone: {snapshot.timezone_display()}",
        f"- ISO timestamp (UTC): {snapshot.iso_utc}",
        "",
        "Use this context when interpreting relative dates like 'last month', 'next week', etc.",
    ]
    # ... prepended to configured system prompt
```

**What the LLM sees in system message:**
```
# Current Date & Time Context
- Today's date: 2025-11-08 (Friday)
- Current time: 3:45 PM EST
- Timezone: Eastern Time (UTC-05:00)
- ISO timestamp (UTC): 2025-11-08T20:45:32Z

Use this context when interpreting relative dates like 'last month', 'next week', etc.

[Your configured system prompt follows...]
```

**Impact:** Even without calling `current_time` tool, the LLM has basic time awareness for date calculations.

### Attachment URL Refresh

Before sending conversation history to the LLM, signed URLs are refreshed:

```python
conversation = await refresh_message_attachments(
    conversation,
    self._repo,
    ttl=self._settings.attachment_signed_url_ttl,
)
```

**Impact:** Attachment URLs in conversation context are always valid, preventing 404 errors.

---

## Configuration: MCP Server Registry

Tools are configured in `data/mcp_servers.json` (or similar):

```json
{
  "servers": [
    {
      "id": "calendar",
      "enabled": true,
      "url": "http://192.168.1.11:9005/mcp",
      "disabled_tools": ["some_tool_to_hide"]
    }
  ]
}
```

### Registry Configuration Fields

| Field | Impact on LLM | Description |
|-------|--------------|-------------|
| `id` | Indirect (prefix) | Server identifier, may prefix tool names |
| `url` | Connection | URL to the external MCP server |
| `enabled` | Direct | If false, tools not exposed to LLM |
| `disabled_tools` | Direct | Listed tools hidden from LLM |

**Key point:** The LLM doesn't see this config directly, but it affects:
1. Which tools are in the tools array
2. What the tool names are (due to prefixing)
3. What the descriptions say (server ID prefix)

---

## Complete Information Flow

**User Input → LLM Decision → Tool Call → Result → Response**

### 1. Request Arrives
User sends: "What do I want to read?"

### 2. Orchestrator Prepares Context
- Loads conversation history from database
- Refreshes attachment URLs
- Prepends time context to system prompt
- Gets tool definitions: `tools_payload = self._mcp_client.get_openai_tools()`

### 3. Tools Sent to LLM
OpenRouter/OpenAI receives:
```json
{
  "model": "anthropic/claude-sonnet-4",
  "messages": [...],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "search_all_tasks",
        "description": "[custom-calendar] Search Google Tasks across every list...",
        "parameters": {...}
      }
    },
    // ... 75 more tools
  ]
}
```

### 4. LLM Makes Decision
Based on:
1. Description: "Search Google Tasks...whenever the user asks about what they want to read"
2. Name: `search_all_tasks` indicates search action
3. Parameters: `query` (string, default="")
4. Example in description: "questions like 'what books do I want to read?'"

**Decision:** Call `search_all_tasks(query="read")`

### 5. Backend Executes
```python
result = await mcp_client.call_tool("search_all_tasks", {"query": "read"})
```

### 6. Result Formatting
```python
text = MCPToolClient.format_tool_result(result)
# Converts MCP CallToolResult to plain text
```

### 7. LLM Receives Tool Result
Added to conversation as a tool message:
```json
{
  "role": "tool",
  "tool_call_id": "call_xyz",
  "content": "Task search for user: 5 matches for 'read'.\n1. \"The Expanse\" series..."
}
```

### 8. LLM Generates Response
Uses tool result to answer user's question naturally.

---

## Summary: What Actually Drives Decisions

**Priority-ranked information the LLM sees:**

1. **Tool Description (50%)** - Your docstring, prefixed with `[server-id]`
   - Explicit triggers: "Call this whenever..."
   - Examples: "questions like..."
   - Imperatives: "MUST", "Always"

2. **Tool Name (25%)** - Action verb + target
   - `search_all_tasks` > `get_tasks` > `tasks`
   - Registry may add prefix: `calendar__search_all_tasks`

3. **Parameter Schema (15%)** - JSON Schema of inputs
   - `properties` - Available parameters
   - `required` - Must provide
   - `type` - Constrains choices (string, integer, array, etc.)

4. **Parameter Descriptions (7%)** - Per-param guidance
   - Embedded in JSON Schema `description` fields

5. **Default Values (3%)** - Signals what's optional/typical
   - `"default": "jck411@gmail.com"` → this user is standard
   - `"default": 25` → reasonable limit

**What the LLM does NOT see:**
- Your Python type hints (converted to JSON Schema)
- The actual Python function code
- Server configuration (`mcp_servers.json`)
- Context tags (used for filtering only)
- Internal comments or implementation details

**The tool description is 50% of the decision.** Make it count.

---

**Document Version:** 1.1
**Focus:** Complete LLM Decision-Making Process (Registry → Schema → Execution)
**Last Updated:** November 8, 2025
