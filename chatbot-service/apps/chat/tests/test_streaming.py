import json
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.chat.models import Chat, Message, MessageDelta



class StreamingTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.client.login(username="test@example.com", password="pass123")
        self.chat = Chat.objects.create(user=self.user, title="Stream Chat", path="stream")

    def test_stream_endpoint_returns_sse(self):
        Message.objects.create(chat=self.chat, role=Message.Role.USER, content="Hello", status=Message.Status.COMPLETED)
        pending = Message.objects.create(
            chat=self.chat, role=Message.Role.ASSISTANT, content="", status=Message.Status.PENDING
        )
        response = self.client.get(reverse("chat:stream", args=[self.chat.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/event-stream")

    def test_stream_creates_pending_if_none_exists(self):
        Message.objects.create(chat=self.chat, role=Message.Role.USER, content="Hello", status=Message.Status.COMPLETED)
        response = self.client.get(reverse("chat:stream", args=[self.chat.id]))
        self.assertEqual(response.status_code, 200)
        assistant_msgs = Message.objects.filter(
            chat=self.chat, role=Message.Role.ASSISTANT
        )
        self.assertGreaterEqual(assistant_msgs.count(), 1)

    def test_stream_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("chat:stream", args=[self.chat.id]))
        self.assertEqual(response.status_code, 302)

    def test_stream_owner_only(self):
        other = User.objects.create_user(email="other@example.com", password="pass123")
        self.client.login(username="other@example.com", password="pass123")
        response = self.client.get(reverse("chat:stream", args=[self.chat.id]))
        self.assertEqual(response.status_code, 404)

    def test_stream_content_yields_events(self):
        Message.objects.create(chat=self.chat, role=Message.Role.USER, content="Hi", status=Message.Status.COMPLETED)
        pending = Message.objects.create(
            chat=self.chat, role=Message.Role.ASSISTANT, content="", status=Message.Status.PENDING
        )
        response = self.client.get(reverse("chat:stream", args=[self.chat.id]))
        content = b"".join(response.streaming_content).decode()
        self.assertIn("data:", content)

    def test_stream_sets_message_status_to_streaming(self):
        Message.objects.create(chat=self.chat, role=Message.Role.USER, content="Hi", status=Message.Status.COMPLETED)
        pending = Message.objects.create(
            chat=self.chat, role=Message.Role.ASSISTANT, content="", status=Message.Status.PENDING
        )
        self.client.get(reverse("chat:stream", args=[self.chat.id]))
        pending.refresh_from_db()
        self.assertEqual(pending.status, Message.Status.STREAMING)

    def test_stream_updates_message_content(self):
        Message.objects.create(chat=self.chat, role=Message.Role.USER, content="Hi", status=Message.Status.COMPLETED)
        pending = Message.objects.create(
            chat=self.chat, role=Message.Role.ASSISTANT, content="", status=Message.Status.PENDING
        )
        self.client.get(reverse("chat:stream", args=[self.chat.id]))
        pending.refresh_from_db()
        self.assertNotEqual(pending.status, Message.Status.PENDING)


class StreamHandlerTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.chat = Chat.objects.create(user=self.user, title="Test", path="test")
        self.msg = Message.objects.create(chat=self.chat, role=Message.Role.ASSISTANT, content="")
        from apps.llm.streaming import StreamHandler
        self.handler = StreamHandler(self.msg)

    def test_handle_text_creates_delta_and_returns_event(self):
        events = list(self.handler._handle_text("Hello"))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "text")
        self.assertEqual(events[0]["content"], "Hello")
        self.assertEqual(MessageDelta.objects.filter(message=self.msg).count(), 1)

    def test_handle_text_accumulates(self):
        list(self.handler._handle_text("Hello "))
        list(self.handler._handle_text("World"))
        self.assertEqual(self.handler.accumulated_content, "Hello World")

    def test_channel_markers_split_reasoning_from_final_text(self):
        events = []
        events.extend(self.handler._handle_content("<|channel>tho"))
        events.extend(self.handler._handle_content("ught\nConsider this."))
        events.extend(self.handler._handle_content("<channel|>Final answer"))
        events.extend(self.handler._handle_stop())

        reasoning = "".join(e["content"] for e in events if e["type"] == "reasoning")
        text = "".join(e["content"] for e in events if e["type"] == "text")
        self.assertEqual(reasoning, "\nConsider this.")
        self.assertEqual(text, "Final answer")

        self.msg.refresh_from_db()
        self.assertEqual(self.msg.content, "Final answer")
        self.assertEqual(self.msg.metadata["reasoning"], "Consider this.")

    def test_split_final_channel_marker_is_not_rendered(self):
        events = []
        events.extend(self.handler._handle_content("<|channel>thoughtThink"))
        events.extend(self.handler._handle_content("<channel|><|chan"))
        events.extend(self.handler._handle_content("nel>finalAnswer"))
        events.extend(self.handler._handle_stop())

        text = "".join(e["content"] for e in events if e["type"] == "text")
        self.assertEqual(text, "Answer")

    def test_handle_done_finalizes_message(self):
        list(self.handler._handle_text("Final "))
        list(self.handler._handle_stop())
        self.msg.refresh_from_db()
        self.assertEqual(self.msg.content, "Final")
        self.assertEqual(self.msg.status, Message.Status.COMPLETED)

    def test_delta_sequence_increments(self):
        list(self.handler._handle_text("A"))
        list(self.handler._handle_text("B"))
        list(self.handler._handle_text("C"))
        deltas = MessageDelta.objects.filter(message=self.msg).order_by("sequence")
        self.assertEqual([d.content for d in deltas], ["A", "B", "C"])
        self.assertEqual([d.sequence for d in deltas], [0, 1, 2])

    def test_handle_reasoning_creates_reasoning_event(self):
        events = list(self.handler._handle_reasoning("I need to think about this"))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "reasoning")
        self.assertEqual(events[0]["content"], "I need to think about this")
        self.assertEqual(self.handler.accumulated_reasoning, "I need to think about this")

    def test_handle_reasoning_accumulates(self):
        list(self.handler._handle_reasoning("Step 1: "))
        list(self.handler._handle_reasoning("Step 2: "))
        self.assertEqual(self.handler.accumulated_reasoning, "Step 1: Step 2: ")

    def test_handle_reasoning_then_text(self):
        list(self.handler._handle_reasoning("Thinking... "))
        list(self.handler._handle_text("Final answer"))
        self.assertEqual(self.handler.accumulated_reasoning, "Thinking... ")
        self.assertEqual(self.handler.accumulated_content, "Final answer")

    def test_reasoning_stored_in_metadata_on_done(self):
        list(self.handler._handle_reasoning("Reasoning trace"))
        list(self.handler._handle_stop())
        self.msg.refresh_from_db()
        self.assertIn("reasoning", self.msg.metadata)
        self.assertEqual(self.msg.metadata["reasoning"], "Reasoning trace")

    def test_no_reasoning_does_not_set_metadata(self):
        list(self.handler._handle_text("No reasoning"))
        list(self.handler._handle_stop())
        self.msg.refresh_from_db()
        reasoning = self.msg.metadata.get("reasoning")
        self.assertIsNone(reasoning)


class MessageDeltaModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.chat = Chat.objects.create(user=self.user, title="Test", path="test")
        self.msg = Message.objects.create(chat=self.chat, role=Message.Role.ASSISTANT, content="")

    def test_delta_sequence_increment(self):
        MessageDelta.objects.create(message=self.msg, sequence=0, content="A")
        MessageDelta.objects.create(message=self.msg, sequence=1, content="B")
        MessageDelta.objects.create(message=self.msg, sequence=2, content="C")
        deltas = MessageDelta.objects.filter(message=self.msg).order_by("sequence")
        self.assertEqual([d.content for d in deltas], ["A", "B", "C"])

    def test_delta_accumulates_content(self):
        parts = ["Hello", " world", "!"]
        for i, part in enumerate(parts):
            MessageDelta.objects.create(message=self.msg, sequence=i, content=part)
        deltas = MessageDelta.objects.filter(message=self.msg).order_by("sequence")
        full = "".join(d.content for d in deltas)
        self.assertEqual(full, "Hello world!")

    def test_delta_raw_event_storage(self):
        delta = MessageDelta.objects.create(
            message=self.msg,
            sequence=0,
            delta_type=MessageDelta.DeltaType.TEXT,
            content="test",
            raw_event={"index": 0, "finish_reason": None},
        )
        self.assertEqual(delta.raw_event["index"], 0)

    def test_delta_done_type(self):
        delta = MessageDelta.objects.create(
            message=self.msg,
            sequence=99,
            delta_type=MessageDelta.DeltaType.DONE,
            content="",
        )
        self.assertEqual(delta.delta_type, "done")

    def test_multiple_messages_deltas(self):
        msg2 = Message.objects.create(chat=self.chat, role=Message.Role.ASSISTANT, content="")
        d1 = MessageDelta.objects.create(message=self.msg, sequence=0, content="First msg")
        d2 = MessageDelta.objects.create(message=msg2, sequence=0, content="Second msg")
        self.assertEqual(MessageDelta.objects.filter(message=self.msg).count(), 1)
        self.assertEqual(MessageDelta.objects.filter(message=msg2).count(), 1)
