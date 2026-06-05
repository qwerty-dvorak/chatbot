from django.test import TestCase

from apps.accounts.models import User
from apps.chat.models import Chat, Message
from apps.compaction.models import ChatCompaction
from apps.compaction.services import compact_chat


class ChatCompactionModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.chat = Chat.objects.create(user=self.user, title="Test", path="test")

    def test_create_compaction(self):
        first = Message.objects.create(chat=self.chat, role=Message.Role.USER, content="First")
        last = Message.objects.create(chat=self.chat, role=Message.Role.ASSISTANT, content="Last")
        comp = ChatCompaction.objects.create(
            chat=self.chat,
            from_message=first,
            to_message=last,
            summary="Test summary",
            facts=[],
            open_questions=[],
            token_count=10,
            model="test-model",
        )
        self.assertEqual(comp.summary, "Test summary")
        self.assertEqual(comp.token_count, 10)

    def test_compaction_str(self):
        first = Message.objects.create(chat=self.chat, role=Message.Role.USER, content="F")
        last = Message.objects.create(chat=self.chat, role=Message.Role.ASSISTANT, content="L")
        comp = ChatCompaction.objects.create(
            chat=self.chat, from_message=first, to_message=last,
            summary="S", facts=[], open_questions=[], token_count=1, model="m",
        )
        self.assertIn(str(self.chat.id)[:8], str(comp))


class CompactionServiceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.chat = Chat.objects.create(user=self.user, title="Compaction Test", path="comptest")

    def test_compact_chat_skips_few_messages(self):
        Message.objects.create(chat=self.chat, role=Message.Role.USER, content="Hi")
        result = compact_chat(self.chat)
        self.assertIsNone(result)

    def test_compact_chat_creates_compaction(self):
        for i in range(12):
            Message.objects.create(
                chat=self.chat,
                role=Message.Role.USER if i % 2 == 0 else Message.Role.ASSISTANT,
                content=f"Message {i}",
                status=Message.Status.COMPLETED,
            )
        result = compact_chat(self.chat)
        self.assertIsNotNone(result)
        self.assertEqual(ChatCompaction.objects.filter(chat=self.chat).count(), 1)

    def test_compaction_does_not_delete_messages(self):
        for i in range(12):
            Message.objects.create(
                chat=self.chat,
                role=Message.Role.USER if i % 2 == 0 else Message.Role.ASSISTANT,
                content=f"Message {i}",
                status=Message.Status.COMPLETED,
            )
        count_before = Message.objects.filter(chat=self.chat).count()
        compact_chat(self.chat)
        count_after = Message.objects.filter(chat=self.chat).count()
        self.assertEqual(count_before, count_after)
