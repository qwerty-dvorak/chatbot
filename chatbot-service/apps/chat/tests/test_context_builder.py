from django.test import TestCase

from apps.accounts.models import User
from apps.chat.models import Chat, Message


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
