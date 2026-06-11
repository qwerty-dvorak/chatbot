import json

from django.test import TestCase, override_settings
from django.urls import reverse
from apps.accounts.models import User
from apps.chat.models import Chat, Message


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
