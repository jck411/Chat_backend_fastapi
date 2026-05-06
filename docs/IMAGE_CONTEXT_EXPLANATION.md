# Image Context – How It Works and What We Fixed

## Overview

The LLM receives images in three main ways:

1. **Direct user uploads** – User attaches images directly to their message.
2. **Tool-returned images** – Tools (like Google Drive) return images that get displayed in chat.
3. **Referenced images** – User asks to see or reason about images from earlier in the conversation.

Originally, tool-returned images were turned into **synthetic user messages**, which made it hard (for both us and the model) to distinguish user-uploaded images from tool images. That confusion has now been fixed and tool images show up as **assistant messages**, with clear URLs and metadata.

This doc combines the original context explanation and the implementation details of the fix.

---

## Current Behaviour (After the Fix)

### 1. Direct User Uploads

When you directly upload an image, the frontend sends a message like:

```json
{
  "role": "user",
  "content": [
    {
      "type": "text",
      "text": "describe the differences between these two"
    },
    {
      "type": "image_url",
      "image_url": {
        "url": "https://storage.googleapis.com/.../image1.jpg"
      },
      "metadata": {
        "attachment_id": "4d540b09e5c84bf9bc171b29b6665683",
        "filename": "acccent_wall_clean.jpg"
      }
    }
  ]
}
```

**The LLM sees**: A `user` message with text and image fragments. This is a real user message; the assistant can clearly treat this as “user uploaded an image”.

> **Note on formats (OpenRouter spec)**
> Per the OpenRouter image inputs docs, the `image_url.url` field may be **either** a regular URL (e.g. a GCS `https://...` link) **or** a base64-encoded `data:` URL for local/private images. Our backend currently always sends **URL-based images** (GCS URLs), which is fully compatible with the OpenRouter multimodal API as described in the official docs ([OpenRouter image inputs docs](https://openrouter.ai/docs/features/multimodal/images)).

### 2. Tool-Returned Images

When a tool (like `gdrive_display_image`) returns an image, its raw output looks like:

```
Image 'accent_wall1.png' from Google Drive displayed in chat!
attachment_id: eea5692eee0e422eabfba3ced61fd7ed
Filename: accent_wall1.png
Size: 1448166 bytes
```

The streaming handler parses this into a `tool` message, and the image fragments are captured separately. In `prepare_messages_for_model()` we now:

- Keep the `tool` message **text-only** (as required by the OpenAI / OpenRouter tool spec).
- Inject the **GCS URLs** for each image directly into the tool message text.
- Store image fragments in `pending_tool_attachments` for the *next* assistant turn.

On the next assistant turn:

1. The assistant generates a normal text reply.
2. The streaming handler injects the pending image fragments into that assistant reply.

So the conversation looks like:

```
[assistant] (tool call)
[tool] Image 'accent_wall1.png' from Google Drive displayed in chat!
       Image URL: https://storage.googleapis.com/.../image.png
[assistant] Here is the image from Google Drive:
           [image.png]
```

**The LLM sees**:

- A `tool` message that is text-only but includes the image URL(s).
- An `assistant` message that contains the images (injected via `pending_tool_attachments`).

### 3. Referenced Images (Still the Hard Problem)

When the user says things like “see image 5” or “compare the 2nd drive image to the 1st upload”, the LLM sees:

1. The new user text: e.g. `"lets look at 5"`.
2. The full conversation history (all previous user uploads + tool images).

Even after the fix, the LLM still **does not get an explicit “this is image 5” reference**. It has to infer which image is being referenced from the surrounding text and prior messages. The distinction between sources is clearer (user vs assistant), but the indexing problem remains open.

---

## Previous Behaviour (What Was Broken)

### Synthetic User Messages for Tool Images

Before the fix, tool-returned images were transformed like this:

1. Tool message with text + image fragments.
2. `prepare_messages_for_model()` created a **synthetic `user` message**:
   - Text: `"Image retrieved from tool result for analysis."`
   - Image fragment(s) attached.
   - Metadata: `source: "tool_attachment_proxy"`, `tool_call_id`, `tool_name`, etc.

That meant the LLM saw:

- A `tool` message (text only, describing what happened).
- A **fake `user` message** containing the image.

### Why That Was Confusing

- **No clean source distinction**:
  - Direct uploads → `user` messages with images.
  - Tool images → **also** `user` messages with images (but secretly synthetic).
- **History is overloaded with images**:
  - When the user says “see image 5”, the model gets all prior user + tool images, but no explicit index or reference.
- **Tool provenance is obscured**:
  - The synthetic user message hides that an image originated from a particular tool call.

This made debugging, reasoning about conversations, and building better “image reference” UX much harder.

---

## Fix – Implementation Summary

### What Was Fixed

- **Problem**: Synthetic user messages were being created for tool-returned images, blurring the line between user uploads and tool images.
- **Solution**: Removed synthetic user message creation. Tool-returned images now appear in **assistant messages**, with GCS URLs included in the `tool` message text for reference.

### High-Level Flow (Now)

1. **Tool returns image** → Handler creates a `tool` message with text + image fragments.
2. **`prepare_messages_for_model()`**:
   - Converts the `tool` message to **text-only**.
   - Appends lines like `Image URL: https://storage.googleapis.com/.../image.png` to the tool text.
   - Stores image fragments in `pending_tool_attachments`.
3. **Next assistant turn**:
   - The assistant generates its reply.
   - The streaming handler injects the pending image fragments, so the reply becomes: “Here is the image…” + `[image]`.

### Key Code Change (`src/backend/chat/streaming/messages.py`)

The synthetic user message creation was removed. Instead, we enrich the tool text with URLs:

```python
# Include GCS URLs in tool message text so LLM can reference them
# Images will be injected into assistant response via pending_tool_attachments
if image_fragments:
    url_lines: list[str] = []
    for fragment in image_fragments:
        image_block = fragment.get("image_url") or {}
        url_value = image_block.get("url")
        metadata_block = fragment.get("metadata", {})
        filename = metadata_block.get("filename", "image") if isinstance(metadata_block, dict) else "image"
        if isinstance(url_value, str) and url_value.strip():
            url_lines.append(f"Image URL: {url_value}")
    if url_lines:
        tool_text_parts.extend(url_lines)
```

---

## What the LLM Sees Now

### Before (Confusing)

```text
[tool] Image 'accent_wall1.png' from Google Drive displayed in chat!
[user] Image retrieved from tool result for analysis.  ← FAKE USER MESSAGE
       [image.png]
[assistant] Here are some images...
```

### After (Clearer)

```text
[tool] Image 'accent_wall1.png' from Google Drive displayed in chat!
       Image URL: https://storage.googleapis.com/.../image.png
[assistant] Here is the image from Google Drive:  ← NATURAL ASSISTANT REPLY
           [image.png]
```

**Distinction is now explicit**:

- **User uploads** → `user` role messages with images.
- **Tool images** → `assistant` role messages with images, backed by text-only `tool` messages that contain URLs.

---

## Remaining Challenges and Future Directions

The core confusion around *referencing* images (e.g., “image 5”) is only partially addressed by this fix. The model still receives a flat history of messages with images, without structured indexing.

Some directions we can pursue:

1. **Add explicit context to tool/assistant messages**
   Enrich assistant messages that contain tool images with more metadata (e.g. “Image #3 from gdrive_search_files call on 2025‑11‑14”).

2. **Mark direct uploads more explicitly**
   Add metadata on user-uploaded images that clearly differentiates them from tool images (e.g. `source: "user_upload"`).

3. **Improve attachment references**
   When the user says “see 5”, translate that into a **specific attachment_id** (or URL) in the message we send to the LLM.

4. **Introduce image indexing**
   Maintain a running index of images in the conversation so “image 5” is unambiguous (e.g., index per source, per tool call, or globally).

5. **Separate “active” vs historical images**
   Track which images are currently “in focus” for the task at hand, and only send those to the LLM when possible, to reduce ambiguity and context size.

This doc should be the single source of truth for how image context works today and what was changed to make tool images less confusing.

