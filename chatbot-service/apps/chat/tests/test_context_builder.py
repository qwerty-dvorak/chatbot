from django.test import TestCase

from apps.accounts.models import User
from apps.chat.models import Chat, Message
from apps.compaction.models import ChatCompaction
from apps.chat.context import ContextBuilder


class ContextBuilderTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.chat = Chat.objects.create(user=self.user, title="Test", path="test")

    def test_messages_ordered_by_created_at(self):
        Message.objects.create(chat=self.chat, role=Message.Role.USER, content="First")
        Message.objects.create(chat=self.chat, role=Message.Role.ASSISTANT, content="Second")
        msgs = Message.objects.filter(chat=self.chat).order_by("created_at")
        self.assertEqual(msgs[0].content, "First")
        self.assertEqual(msgs[1].content, "Second")

    def test_message_roles_choices(self):
        for role in ["system", "user", "assistant", "tool"]:
            msg = Message.objects.create(chat=self.chat, role=role, content=role)
            self.assertEqual(msg.role, role)

    def test_message_status_choices(self):
        for status in ["pending", "streaming", "completed", "failed", "cancelled"]:
            msg = Message.objects.create(chat=self.chat, role=Message.Role.USER, content=status, status=status)
            self.assertEqual(msg.status, status)

    def test_compaction_replaces_covered_history(self):
        old_user = Message.objects.create(
            chat=self.chat, role=Message.Role.USER, content="Old secret detail"
        )
        old_assistant = Message.objects.create(
            chat=self.chat, role=Message.Role.ASSISTANT, content="Old response"
        )
        latest = Message.objects.create(
            chat=self.chat, role=Message.Role.USER, content="Current question"
        )
        ChatCompaction.objects.create(
            chat=self.chat,
            from_message=old_user,
            to_message=old_assistant,
            summary="The earlier discussion established the retained detail.",
            facts=["The user has a retained preference."],
            open_questions=["Confirm the next step."],
            token_count=20,
            model="test-model",
        )

        messages, _ = ContextBuilder(self.chat, self.user).build(latest.content, latest)
        serialized = str(messages)

        self.assertIn("earlier discussion established", serialized)
        self.assertIn("retained preference", serialized)
        self.assertNotIn("Old secret detail", serialized)
        self.assertEqual(
            sum(
                1
                for message in messages
                if message["role"] == Message.Role.USER
                and message["content"] == "Current question"
            ),
            1,
        )
