# How LLMs Interpret Tool Parameters

## Default Values vs Null

When the LLM sees a parameter in a tool schema, the `default` value strongly influences its behavior:

### Example 1: `user_email` (WITH description)
```json
{
  "user_email": {
    "type": "string",
    "default": "jck411@gmail.com",
    "description": "Email address of the user whose calendar to search"
  }
}
```

**LLM thinks:**
- ✅ "This parameter has a sensible default"
- ✅ "The description tells me it's for identifying which user's calendar"
- ✅ "I don't need to specify it unless talking about a different user"
- ✅ "The system already knows the user is jck411@gmail.com"
- **Result:** LLM includes it to be explicit, but knows it's not critical

**Without description, the LLM would be guessing:**
- ❌ "Is this an email TO send? FROM address? Search parameter?"
- ❌ "Why is there a default email here?"
- ❌ "Should I always include this or can I omit it?"

---

### Example 2: `time_min` with `null`
```json
{
  "time_min": {
    "anyOf": [
      {"type": "string"},
      {"type": "null"}
    ],
    "default": null
  }
}
```

**LLM thinks:**
- ⚠️ "This parameter is optional (defaults to null)"
- ⚠️ "If I don't specify it, the tool will use some internal logic"
- ⚠️ "But the user said 'today', so I SHOULD provide time bounds"
- **Result:** LLM decides to override the null default with actual values

---

### Example 3: `max_results`
```json
{
  "max_results": {
    "type": "integer",
    "default": 25
  }
}
```

**LLM thinks:**
- ✅ "This has a reasonable default (25 results)"
- ✅ "User didn't mention needing more or fewer results"
- ✅ "I'll let the default handle this"
- **Result:** LLM omits this parameter entirely

---

## Decision Matrix

| Parameter State | LLM Behavior | Example |
|----------------|--------------|---------|
| `default: "value"` | Uses default unless user implies otherwise | `user_email: "jck411@gmail.com"` |
| `default: null` + user mentions it | Provides specific value | `time_min: "2025-11-10T00:00:00"` |
| `default: null` + not relevant | Omits parameter | `query: null` (no keyword search) |
| `default: 25` + not mentioned | Omits parameter | `max_results: 25` (reasonable limit) |
| No default (required) | MUST provide value | Would error if omitted |

---

## Why the LLM Included Those Specific Parameters

From your conversation log:

```json
{
  "user_email": "jck411@gmail.com",     // Included (could have omitted, being explicit)
  "time_min": "2025-11-10T00:00:00-05:00",  // MUST include (user said "today")
  "time_max": "2025-11-10T23:59:59-05:00"   // MUST include (user said "today")
  // Omitted: calendar_id, max_results, query, detailed (all have good defaults)
}
```

### The LLM's reasoning:
1. **"today"** in the query → Need to constrain time range
2. `time_min` defaults to `null` → But I should override it for "today"
3. `time_max` defaults to `null` → But I should override it for "today"
4. `user_email` has default → Could omit, but being explicit is safer
5. `max_results` has default `25` → That's fine, omit it
6. `query` is `null` → No keyword search needed, omit it
7. `calendar_id` is `null` → Search all calendars (good), omit it

---

## Key Insight

**Without description:**
```json
{"user_email": {"type": "string", "default": "jck411@gmail.com"}}
```
LLM is confused: *"What is this email for? Why is it there? Should I change it?"*

**With description:**
```json
{
  "user_email": {
    "type": "string",
    "default": "jck411@gmail.com",
    "description": "Email address of the user whose calendar to search"
  }
}
```
LLM understands: *"Ah, it's for identifying which user. Default is current user."*

---

**`default: null`** tells the LLM:
> "This is optional. Only provide it if the user's request requires it."

**`default: "actual_value"`** tells the LLM:
> "This has a sensible default. You rarely need to change it."

**No default** tells the LLM:
> "This is REQUIRED. You must always provide a value."

**Required + No default + No description** tells the LLM:
> "I have no idea what to put here. I'll probably guess wrong or error out."

The LLM uses **semantic understanding** to decide when to override defaults - but only if descriptions provide that semantic context.
