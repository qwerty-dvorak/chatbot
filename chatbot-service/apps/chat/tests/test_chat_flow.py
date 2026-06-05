import uuid

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.chat.models import Chat, ChatShare, Message, MessageDelta, Vote


class ChatModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")

    def test_create_chat(self):
        chat = Chat.objects.create(user=self.user, title="Test Chat", path="test-path")
        self.assertEqual(chat.title, "Test Chat")
        self.assertEqual(chat.user, self.user)
        self.assertFalse(chat.archived)
        self.assertIsNotNone(chat.created_at)

    def test_chat_str(self):
        chat = Chat.objects.create(user=self.user, title="Test Chat", path="test-path")
        self.assertEqual(str(chat), "Test Chat")

    def test_chat_unique_user_path(self):
        Chat.objects.create(user=self.user, title="Chat 1", path="same-path")
        with self.assertRaises(Exception):
            Chat.objects.create(user=self.user, title="Chat 2", path="same-path")

    def test_chat_archived_default(self):
        chat = Chat.objects.create(user=self.user, title="Chat", path="path")
        self.assertFalse(chat.archived)

    def test_chat_ordering(self):
        Chat.objects.create(user=self.user, title="Older", path="old")
        Chat.objects.create(user=self.user, title="Newer", path="new")
        chats = Chat.objects.filter(user=self.user).order_by("-updated_at")
        self.assertEqual(chats[0].title, "Newer")


class MessageModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.chat = Chat.objects.create(user=self.user, title="Test Chat", path="test")

    def test_create_user_message(self):
        msg = Message.objects.create(
            chat=self.chat,
            role=Message.Role.USER,
            content="Hello, world!",
            status=Message.Status.COMPLETED,
        )
        self.assertEqual(msg.role, "user")
        self.assertEqual(msg.content, "Hello, world!")
        self.assertEqual(msg.status, Message.Status.COMPLETED)
        self.assertIsNotNone(msg.created_at)

    def test_create_assistant_message(self):
        msg = Message.objects.create(
            chat=self.chat,
            role=Message.Role.ASSISTANT,
            content="Hi there!",
            status=Message.Status.PENDING,
        )
        self.assertEqual(msg.role, "assistant")
        self.assertEqual(msg.status, "pending")

    def test_message_str(self):
        msg = Message.objects.create(chat=self.chat, role=Message.Role.USER, content="Hello!")
        self.assertIn("Hello!", str(msg))

    def test_message_empty_content(self):
        msg = Message.objects.create(chat=self.chat, role=Message.Role.USER, content="")
        self.assertEqual(msg.content, "")

    def test_message_defaults(self):
        msg = Message.objects.create(chat=self.chat, role=Message.Role.SYSTEM, content="system msg")
        self.assertEqual(msg.status, Message.Status.COMPLETED)
        self.assertEqual(msg.tool_invocations, {})
        self.assertEqual(msg.attachments, [])
        self.assertEqual(msg.metadata, {})
        self.assertIsNone(msg.parent_message)
        self.assertIsNone(msg.completed_at)

    def test_message_self_reference(self):
        parent = Message.objects.create(chat=self.chat, role=Message.Role.USER, content="parent")
        child = Message.objects.create(chat=self.chat, role=Message.Role.ASSISTANT, content="child", parent_message=parent)
        self.assertEqual(child.parent_message, parent)
        self.assertIn(child, parent.child_messages.all())


class MessageDeltaModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.chat = Chat.objects.create(user=self.user, title="Chat", path="path")
        self.msg = Message.objects.create(chat=self.chat, role=Message.Role.ASSISTANT, content="")

    def test_create_delta(self):
        delta = MessageDelta.objects.create(
            message=self.msg, sequence=1, delta_type=MessageDelta.DeltaType.TEXT, content="Hello"
        )
        self.assertEqual(delta.sequence, 1)
        self.assertEqual(delta.content, "Hello")

    def test_delta_unique_sequence(self):
        MessageDelta.objects.create(message=self.msg, sequence=1, content="First")
        with self.assertRaises(Exception):
            MessageDelta.objects.create(message=self.msg, sequence=1, content="Duplicate")

    def test_delta_types(self):
        for dt in ["text", "tool_call", "tool_result", "error", "done"]:
            delta = MessageDelta.objects.create(message=self.msg, sequence=MessageDelta.objects.count() + 1, delta_type=dt)
            self.assertEqual(delta.delta_type, dt)


class VoteModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.chat = Chat.objects.create(user=self.user, title="Chat", path="path")
        self.msg = Message.objects.create(chat=self.chat, role=Message.Role.ASSISTANT, content="response")

    def test_create_upvote(self):
        vote = Vote.objects.create(chat=self.chat, message=self.msg, user=self.user, is_upvoted=True)
        self.assertTrue(vote.is_upvoted)

    def test_unique_user_message_vote(self):
        Vote.objects.create(chat=self.chat, message=self.msg, user=self.user, is_upvoted=True)
        with self.assertRaises(Exception):
            Vote.objects.create(chat=self.chat, message=self.msg, user=self.user, is_upvoted=False)


class ChatShareModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.chat = Chat.objects.create(user=self.user, title="Chat", path="path")

    def test_create_share(self):
        share = ChatShare.objects.create(
            chat=self.chat, user=self.user, token=uuid.uuid4().hex
        )
        self.assertFalse(share.revoked)
        self.assertIsNotNone(share.token)

    def test_share_token_unique(self):
        ChatShare.objects.create(chat=self.chat, user=self.user, token="same-token")
        with self.assertRaises(Exception):
            ChatShare.objects.create(chat=self.chat, user=self.user, token="same-token")

    def test_share_revocation(self):
        share = ChatShare.objects.create(chat=self.chat, user=self.user, token=uuid.uuid4().hex)
        share.revoked = True
        share.save(update_fields=["revoked"])
        self.assertTrue(ChatShare.objects.get(id=share.id).revoked)


class ChatListViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.client.login(username="test@example.com", password="pass123")

    def test_list_shows_user_chats(self):
        Chat.objects.create(user=self.user, title="My Chat", path="my-chat")
        response = self.client.get(reverse("chat:list"))
        self.assertContains(response, "My Chat")

    def test_list_does_not_show_other_user_chats(self):
        other = User.objects.create_user(email="other@example.com", password="pass123")
        Chat.objects.create(user=other, title="Other's Chat", path="other-chat")
        response = self.client.get(reverse("chat:list"))
        self.assertNotContains(response, "Other's Chat")

    def test_list_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("chat:list"))
        self.assertEqual(response.status_code, 302)

    def test_list_empty(self):
        response = self.client.get(reverse("chat:list"))
        self.assertContains(response, "No chats yet")


class ChatCreateViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.client.login(username="test@example.com", password="pass123")

    def test_create_chat(self):
        response = self.client.post(reverse("chat:new"), {"title": "New Chat"}, follow=True)
        self.assertTrue(Chat.objects.filter(title="New Chat", user=self.user).exists())

    def test_create_assigns_path(self):
        response = self.client.post(reverse("chat:new"), {"title": "Path Chat"}, follow=True)
        chat = Chat.objects.get(title="Path Chat", user=self.user)
        self.assertIsNotNone(chat.path)
        self.assertEqual(len(chat.path), 8)

    def test_create_assigns_user(self):
        response = self.client.post(reverse("chat:new"), {"title": "User Chat"}, follow=True)
        chat = Chat.objects.get(title="User Chat")
        self.assertEqual(chat.user, self.user)

    def test_get_create_page(self):
        response = self.client.get(reverse("chat:new"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "chat/chat_new.html")


class ChatDetailViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.client.login(username="test@example.com", password="pass123")
        self.chat = Chat.objects.create(user=self.user, title="Detail Chat", path="detail")

    def test_detail_shows_messages(self):
        Message.objects.create(chat=self.chat, role=Message.Role.USER, content="Hello")
        Message.objects.create(chat=self.chat, role=Message.Role.ASSISTANT, content="Hi")
        response = self.client.get(reverse("chat:detail", args=[self.chat.id]))
        self.assertContains(response, "Hello")
        self.assertContains(response, "Hi")

    def test_detail_show_form(self):
        response = self.client.get(reverse("chat:detail", args=[self.chat.id]))
        self.assertContains(response, "Send")

    def test_detail_owner_only(self):
        other = User.objects.create_user(email="other@example.com", password="pass123")
        self.client.login(username="other@example.com", password="pass123")
        response = self.client.get(reverse("chat:detail", args=[self.chat.id]))
        self.assertEqual(response.status_code, 404)

    def test_post_message_creates_user_and_assistant(self):
        response = self.client.post(
            reverse("chat:detail", args=[self.chat.id]),
            {"content": "Test message"},
            follow=True,
        )
        msgs = Message.objects.filter(chat=self.chat).order_by("created_at")
        self.assertEqual(msgs.count(), 2)
        self.assertEqual(msgs[0].role, "user")
        self.assertEqual(msgs[0].content, "Test message")
        self.assertEqual(msgs[1].role, "assistant")
        self.assertEqual(msgs[1].status, "pending")

    def test_post_empty_message_rejected(self):
        response = self.client.post(
            reverse("chat:detail", args=[self.chat.id]),
            {"content": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Message content or attachment is required.")

    def test_detail_redirects_to_self_after_post(self):
        response = self.client.post(
            reverse("chat:detail", args=[self.chat.id]),
            {"content": "Hello"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("chat:detail", args=[self.chat.id]))


class ChatArchiveViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.client.login(username="test@example.com", password="pass123")
        self.chat = Chat.objects.create(user=self.user, title="Archivable", path="archivable")

    def test_archive_chat(self):
        self.assertFalse(Chat.objects.get(id=self.chat.id).archived)
        self.client.post(reverse("chat:archive", args=[self.chat.id]))
        self.assertTrue(Chat.objects.get(id=self.chat.id).archived)

    def test_restore_chat(self):
        self.chat.archived = True
        self.chat.save()
        self.client.post(reverse("chat:archive", args=[self.chat.id]))
        self.assertFalse(Chat.objects.get(id=self.chat.id).archived)

    def test_archive_redirects(self):
        response = self.client.post(reverse("chat:archive", args=[self.chat.id]))
        self.assertRedirects(response, reverse("chat:list"))

    def test_archive_other_user_denied(self):
        other = User.objects.create_user(email="other@example.com", password="pass123")
        self.client.login(username="other@example.com", password="pass123")
        response = self.client.post(reverse("chat:archive", args=[self.chat.id]))
        self.assertEqual(response.status_code, 404)


class ChatShareViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.client.login(username="test@example.com", password="pass123")
        self.chat = Chat.objects.create(user=self.user, title="Shareable", path="shareable")

    def test_create_share(self):
        self.client.post(reverse("chat:share", args=[self.chat.id]))
        self.assertTrue(ChatShare.objects.filter(chat=self.chat, revoked=False).exists())

    def test_create_share_revokes_old(self):
        old = ChatShare.objects.create(
            chat=self.chat, user=self.user, token="old-token", revoked=False
        )
        self.client.post(reverse("chat:share", args=[self.chat.id]))
        old.refresh_from_db()
        self.assertTrue(old.revoked)

    def test_share_owner_only(self):
        other = User.objects.create_user(email="other@example.com", password="pass123")
        self.client.login(username="other@example.com", password="pass123")
        response = self.client.post(reverse("chat:share", args=[self.chat.id]))
        self.assertEqual(response.status_code, 404)

    def test_share_page_shows_link(self):
        share = ChatShare.objects.create(
            chat=self.chat, user=self.user, token="test-token-123", revoked=False
        )
        response = self.client.get(reverse("chat:share", args=[self.chat.id]))
        self.assertContains(response, "test-token-123")


class SharedChatViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.chat = Chat.objects.create(user=self.user, title="Shared Chat", path="shared")
        self.share = ChatShare.objects.create(
            chat=self.chat, user=self.user, token="public-token", revoked=False
        )
        self.msg = Message.objects.create(chat=self.chat, role=Message.Role.USER, content="Public message")

    def test_shared_view_shows_messages(self):
        response = self.client.get(reverse("chat-shared", args=["public-token"]))
        self.assertContains(response, "Public message")

    def test_shared_view_does_not_require_login(self):
        self.client.logout()
        response = self.client.get(reverse("chat-shared", args=["public-token"]))
        self.assertEqual(response.status_code, 200)

    def test_revoked_share_returns_404(self):
        self.share.revoked = True
        self.share.save()
        response = self.client.get(reverse("chat-shared", args=["public-token"]))
        self.assertEqual(response.status_code, 404)

    def test_invalid_token_returns_404(self):
        response = self.client.get(reverse("chat-shared", args=["nonexistent"]))
        self.assertEqual(response.status_code, 404)
