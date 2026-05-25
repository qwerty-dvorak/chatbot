from django.test import TestCase

from apps.accounts.models import User
from apps.chat.models import Chat, Message, MessageDelta


class MessageDeltaTest(TestCase):
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
