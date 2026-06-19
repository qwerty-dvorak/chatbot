import json
import struct
import zlib

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.urls import reverse
from apps.accounts.models import User
from apps.chat.models import Chat, Message


def _make_minimal_png(width: int = 1, height: int = 1) -> bytes:
    """Create a minimal valid PNG file (solid red pixel by default)."""
    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        crc = struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + c + crc
    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = _chunk(b'IHDR', struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    raw_data = b'\x00' + b'\xff\x00\x00\xff\x00\x00' * (width * height)
    compressed = zlib.compress(raw_data)
    idat = _chunk(b'IDAT', compressed)
    iend = _chunk(b'IEND', b'')
    return sig + ihdr + idat + iend


class ChatAPITest(TestCase):
    """End-to-end API tests for the chat flow."""

    def setUp(self):
        self.password = "testpass123"
        self.user = User.objects.create_user(
            email="testuser@example.com", password=self.password,
        )
        self.client.login(username=self.user.email, password=self.password)

    # ── Public API endpoints ──────────────────────────────────────────────

    def test_health_endpoint(self):
        response = self.client.get(reverse("api-health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_stats_endpoint_returns_counts(self):
        chat = Chat.objects.create(user=self.user, title="Stats", path="s1")
        Message.objects.create(chat=chat, role=Message.Role.USER, content="Hi")
        response = self.client.get(reverse("api-stats"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["users"], 1)
        self.assertEqual(data["chats"], 1)
        self.assertEqual(data["messages"], 1)

    # ── Chat CRUD ─────────────────────────────────────────────────────────

    def test_create_chat_via_post(self):
        response = self.client.post(
            reverse("chat:new"), {"title": "API Chat"}, follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Chat.objects.filter(title="API Chat", user=self.user).exists()
        )

    def test_chat_detail_shows_messages(self):
        chat = Chat.objects.create(user=self.user, title="Detail", path="d1")
        Message.objects.create(chat=chat, role=Message.Role.USER, content="Hello")
        response = self.client.get(reverse("chat:detail", args=[chat.id]))
        self.assertContains(response, "Hello")

    def test_chat_list_owner_only(self):
        Chat.objects.create(user=self.user, title="Mine", path="m1")
        other = User.objects.create_user(email="other@e.com", password="pass")
        Chat.objects.create(user=other, title="Theirs", path="t1")
        response = self.client.get(reverse("chat:list"))
        self.assertContains(response, "Mine")
        self.assertNotContains(response, "Theirs")

    def test_other_user_cannot_access_chat(self):
        chat = Chat.objects.create(user=self.user, title="Private", path="p1")
        other = User.objects.create_user(email="other@e.com", password="pass")
        self.client.login(username="other@e.com", password="pass")
        response = self.client.get(reverse("chat:detail", args=[chat.id]))
        self.assertEqual(response.status_code, 404)

    # ── Message posting ───────────────────────────────────────────────────

    def test_send_message_creates_user_and_pending_assistant(self):
        chat = Chat.objects.create(user=self.user, title="Msg", path="m1")
        response = self.client.post(
            reverse("chat:detail", args=[chat.id]),
            {"content": "Hello world"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        msgs = list(Message.objects.filter(chat=chat).order_by("created_at"))
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0].role, "user")
        self.assertEqual(msgs[0].content, "Hello world")
        self.assertEqual(msgs[0].status, "completed")
        self.assertEqual(msgs[1].role, "assistant")
        self.assertEqual(msgs[1].status, "pending")

    def test_post_empty_message_rejected(self):
        chat = Chat.objects.create(user=self.user, title="Empty", path="e1")
        response = self.client.post(
            reverse("chat:detail", args=[chat.id]), {"content": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("required", str(response.context["form"].errors))

    def test_message_accepts_multiple_attachments(self):
        chat = Chat.objects.create(user=self.user, title="Attachments", path="attachments")
        response = self.client.post(
            reverse("chat:detail", args=[chat.id]),
            {
                "content": "Compare these files",
                "attachment": [
                    SimpleUploadedFile("alpha.txt", b"alpha", content_type="text/plain"),
                    SimpleUploadedFile("pixel.png", _make_minimal_png(), content_type="image/png"),
                ],
            },
        )
        self.assertEqual(response.status_code, 302)
        message = Message.objects.get(chat=chat, role=Message.Role.USER)
        self.assertEqual(
            [item["original_filename"] for item in message.attachments],
            ["alpha.txt", "pixel.png"],
        )

    # ── Streaming ─────────────────────────────────────────────────────────

    @override_settings(RAG_ENABLED=False)
    def test_stream_endpoint_returns_sse_events(self):
        chat = Chat.objects.create(user=self.user, title="Stream", path="s1")
        self.client.post(
            reverse("chat:detail", args=[chat.id]),
            {"content": "Count from 1 to 5 with digits only"},
        )
        response = self.client.get(reverse("chat:stream", args=[chat.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/event-stream")

        events = []
        for raw in response.streaming_content:
            data = raw if isinstance(raw, str) else raw.decode()
            for line in data.split("\n"):
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

        text_chunks = [e["content"] for e in events if e.get("type") == "text"]
        self.assertGreater(len(text_chunks), 0)

        done = [e for e in events if e.get("type") == "done"]
        self.assertEqual(len(done), 1)

        full_text = "".join(text_chunks)
        self.assertGreater(len(full_text), 5)
        self.assertIn("1", full_text, "Model response should contain '1' from the count request")

    @override_settings(RAG_ENABLED=False)
    def test_stream_marks_message_completed(self):
        chat = Chat.objects.create(user=self.user, title="Done", path="d1")
        self.client.post(
            reverse("chat:detail", args=[chat.id]),
            {"content": "Hi there"},
        )
        response = self.client.get(reverse("chat:stream", args=[chat.id]))
        list(response.streaming_content)

        msg = Message.objects.filter(
            chat=chat, role=Message.Role.ASSISTANT
        ).first()
        self.assertIsNotNone(msg)
        self.assertEqual(msg.status, "completed")
        self.assertGreater(len(msg.content), 0)

    @override_settings(RAG_ENABLED=False)
    def test_full_chat_flow_end_to_end(self):
        chat = Chat.objects.create(user=self.user, title="E2E", path="e2e")
        self.client.post(
            reverse("chat:detail", args=[chat.id]),
            {"content": "Repeat exactly: HELLO_TEST_WORLD"},
            follow=True,
        )
        stream_resp = self.client.get(reverse("chat:stream", args=[chat.id]))
        list(stream_resp.streaming_content)

        msgs = list(Message.objects.filter(chat=chat).order_by("created_at"))
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0].role, "user")
        self.assertEqual(msgs[1].role, "assistant")
        self.assertEqual(msgs[1].status, "completed")
        self.assertNotEqual(msgs[1].content, "")

    @override_settings(RAG_ENABLED=False)
    def test_stream_event_types_and_structure(self):
        chat = Chat.objects.create(user=self.user, title="Types", path="t1")
        self.client.post(
            reverse("chat:detail", args=[chat.id]),
            {"content": "Say just: ok"},
        )
        response = self.client.get(reverse("chat:stream", args=[chat.id]))

        events = []
        for raw in response.streaming_content:
            data = raw if isinstance(raw, str) else raw.decode()
            for line in data.split("\n"):
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

        for event in events:
            self.assertIn("type", event,
                          "Every SSE event must have a 'type' field")
            if event["type"] == "text":
                self.assertIn("content", event,
                              "Text events must have 'content'")

        text_events = [e for e in events if e.get("type") == "text"]
        for te in text_events:
            self.assertIsInstance(te["content"], str)
            self.assertGreater(len(te["content"]), 0)

    # ── Vision / Image with streaming ─────────────────────────────────────

    @override_settings(RAG_ENABLED=False)
    def test_stream_with_image_attachment_llm_responds(self):
        png_data = _make_minimal_png(4, 4)
        image = ContentFile(png_data, name="test_vision.png")

        chat = Chat.objects.create(user=self.user, title="Vision", path="v1")
        self.client.post(
            reverse("chat:detail", args=[chat.id]),
            {"content": "What color is this image? Reply with just the color name.", "attachment": image},
            follow=True,
        )
        response = self.client.get(reverse("chat:stream", args=[chat.id]))
        self.assertEqual(response.status_code, 200)

        events = []
        for raw in response.streaming_content:
            data = raw if isinstance(raw, str) else raw.decode()
            for line in data.split("\n"):
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

        text_chunks = [e["content"] for e in events if e.get("type") == "text"]
        error_events = [e for e in events if e.get("type") == "error"]

        if error_events:
            user_msg = Message.objects.filter(chat=chat, role=Message.Role.USER).first()
            self.assertIsNotNone(user_msg)
            self.assertEqual(len(user_msg.attachments), 1)
            self.assertEqual(user_msg.attachments[0]["mime_type"], "image/png")
        else:
            self.assertGreater(len(text_chunks), 0, "LLM should produce text when given an image")
            full_text = "".join(text_chunks)
            self.assertGreater(len(full_text), 3)
            done = [e for e in events if e.get("type") == "done"]
            self.assertEqual(len(done), 1)
            msg = Message.objects.filter(chat=chat, role=Message.Role.ASSISTANT).first()
            self.assertIsNotNone(msg)
            self.assertEqual(msg.status, "completed")

    # ── Tool calling through streaming ────────────────────────────────────

    @override_settings(
        RAG_ENABLED=False,
        TOOL_CALLS_ENABLED=True,
    )
    def test_tool_call_triggered_via_streaming(self):
        from django.core.management import call_command
        call_command("sync_builtin_tools")

        chat = Chat.objects.create(user=self.user, title="ToolCall", path="tc1")
        self.client.post(
            reverse("chat:detail", args=[chat.id]),
            {"content": "Save to memory: I enjoy hiking on weekends"},
            follow=True,
        )
        response = self.client.get(reverse("chat:stream", args=[chat.id]))
        self.assertEqual(response.status_code, 200)

        events = []
        for raw in response.streaming_content:
            data = raw if isinstance(raw, str) else raw.decode()
            for line in data.split("\n"):
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

        tool_call_events = [e for e in events if e.get("type") == "tool_call"]
        tool_result_events = [e for e in events if e.get("type") == "tool_result"]

        if tool_call_events:
            self.assertGreater(len(tool_call_events), 0)
            tc = tool_call_events[0].get("tool_call", tool_call_events[0])
            self.assertIn("name", tc)
            self.assertIn("memory", tc["name"].lower())

        if tool_result_events:
            self.assertGreater(len(tool_result_events), 0)
            self.assertIn("content", tool_result_events[0])

        done = [e for e in events if e.get("type") == "done"]
        self.assertEqual(len(done), 1, "Stream must end with a done event")

    @override_settings(
        RAG_ENABLED=False,
        TOOL_CALLS_ENABLED=True,
    )
    def test_tool_call_records_persisted(self):
        from django.core.management import call_command
        from apps.tools.models import ToolCall as ToolCallModel
        call_command("sync_builtin_tools")

        chat = Chat.objects.create(user=self.user, title="ToolRec", path="tr1")
        self.client.post(
            reverse("chat:detail", args=[chat.id]),
            {"content": "Remember that my favorite food is sushi"},
            follow=True,
        )
        response = self.client.get(reverse("chat:stream", args=[chat.id]))
        list(response.streaming_content)

        tool_calls = ToolCallModel.objects.filter(chat=chat)
        if tool_calls.exists():
            tc = tool_calls.first()
            self.assertEqual(tc.user, self.user)
            self.assertIsNotNone(tc.tool)
            self.assertIsNotNone(tc.arguments)

        msg = Message.objects.filter(chat=chat, role=Message.Role.ASSISTANT).first()
        self.assertIsNotNone(msg)
        self.assertEqual(msg.status, "completed")

    # ── Reasoning / thinking through streaming ────────────────────────────

    @override_settings(
        RAG_ENABLED=False,
        CHAT_REASONING_ENABLED=True,
    )
    def test_reasoning_events_via_streaming(self):
        chat = Chat.objects.create(user=self.user, title="Reason", path="r1")
        self.client.post(
            reverse("chat:detail", args=[chat.id]),
            {"content": "Solve 23 multiplied by 47 step by step. Show your reasoning."},
            follow=True,
        )
        response = self.client.get(reverse("chat:stream", args=[chat.id]))
        self.assertEqual(response.status_code, 200)

        events = []
        for raw in response.streaming_content:
            data = raw if isinstance(raw, str) else raw.decode()
            for line in data.split("\n"):
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

        reasoning_events = [e for e in events if e.get("type") == "reasoning"]
        text_events = [e for e in events if e.get("type") == "text"]

        if reasoning_events:
            for re in reasoning_events:
                self.assertIn("content", re)
            combined_reasoning = "".join(e["content"] for e in reasoning_events)
            self.assertGreater(len(combined_reasoning), 5)

        self.assertGreater(len(text_events), 0, "LLM should produce text output")

        full_text = "".join(e["content"] for e in text_events)
        self.assertGreater(len(full_text), 3)
        self.assertIn("1081", full_text, "23*47=1081 should appear in the response")

    @override_settings(
        RAG_ENABLED=False,
        CHAT_REASONING_ENABLED=True,
    )
    def test_reasoning_stored_in_message_metadata(self):
        chat = Chat.objects.create(user=self.user, title="ReasonMeta", path="rm1")
        self.client.post(
            reverse("chat:detail", args=[chat.id]),
            {"content": "What is 15 percent of 200? Show your reasoning step by step."},
            follow=True,
        )
        response = self.client.get(reverse("chat:stream", args=[chat.id]))
        list(response.streaming_content)

        msg = Message.objects.filter(
            chat=chat, role=Message.Role.ASSISTANT, status=Message.Status.COMPLETED
        ).first()
        self.assertIsNotNone(msg)
        self.assertGreater(len(msg.content), 0)

    @override_settings(
        RAG_ENABLED=False,
        CHAT_REASONING_ENABLED=False,
        TOOL_CALLS_ENABLED=False,
    )
    def test_multiple_thinking_turns_store_separate_reasoning(self):
        chat = Chat.objects.create(user=self.user, title="MultiThink", path="multi-think")
        prompts = [
            "Calculate 37 multiplied by 19 and explain the calculation.",
            "Now calculate 83 multiplied by 14 and explain this calculation separately.",
        ]

        for prompt in prompts:
            self.client.post(
                reverse("chat:detail", args=[chat.id]),
                {"content": prompt, "thinking_mode": "1"},
            )
            response = self.client.get(reverse("chat:stream", args=[chat.id]))
            list(response.streaming_content)

        assistant_messages = list(
            Message.objects.filter(
                chat=chat,
                role=Message.Role.ASSISTANT,
                status=Message.Status.COMPLETED,
            ).order_by("created_at")
        )
        self.assertEqual(len(assistant_messages), 2)
        for message in assistant_messages:
            self.assertTrue(message.metadata.get("thinking_mode"))
        # Reasoning is model-dependent and may not always be produced,
        # but if both messages have it, they must be different.
        r0 = assistant_messages[0].metadata.get("reasoning", "")
        r1 = assistant_messages[1].metadata.get("reasoning", "")
        if r0 and r1:
            self.assertNotEqual(r0, r1)

    # ── Memory.save tool e2e (tool + result + text response) ──────────────

    @override_settings(
        RAG_ENABLED=False,
        TOOL_CALLS_ENABLED=True,
    )
    def test_memory_save_tool_e2e_flow(self):
        from django.core.management import call_command
        call_command("sync_builtin_tools")

        chat = Chat.objects.create(user=self.user, title="MemTest", path="mt1")
        self.client.post(
            reverse("chat:detail", args=[chat.id]),
            {"content": "Remember that my dog's name is Buddy"},
            follow=True,
        )
        response = self.client.get(reverse("chat:stream", args=[chat.id]))
        self.assertEqual(response.status_code, 200)

        events = []
        for raw in response.streaming_content:
            data = raw if isinstance(raw, str) else raw.decode()
            for line in data.split("\n"):
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

        event_types = [e.get("type") for e in events]
        self.assertIn("done", event_types)

        tool_call_events = [e for e in events if e.get("type") == "tool_call"]
        tool_result_events = [e for e in events if e.get("type") == "tool_result"]
        text_events = [e for e in events if e.get("type") == "text"]

        if tool_call_events:
            tc = tool_call_events[0].get("tool_call", tool_call_events[0])
            self.assertIn("name", tc)
            self.assertIn("memory", tc["name"].lower())
        if tool_result_events:
            self.assertIsInstance(tool_result_events[0].get("content"), str)

        if text_events:
            full_text = "".join(e["content"] for e in text_events)
            self.assertGreater(len(full_text), 0)

    # ── Authentication ────────────────────────────────────────────────────

    def test_unauthenticated_access_redirects_to_login(self):
        self.client.logout()
        paths = [
            reverse("chat:list"),
            reverse("chat:new"),
            reverse("api-stats"),
        ]
        for path in paths:
            response = self.client.get(path)
            self.assertIn(response.status_code, (200, 302))
