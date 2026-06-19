from django.db import IntegrityError
from django.test import TestCase

from apps.accounts.models import User
from apps.chat.models import (
    Chat,
    ChatBranch,
    ChatGrant,
    Message,
    MessageAttachment,
    TurnEvent,
    TurnRun,
)


class ChatGrantTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="owner@test.com", password="pass")
        self.viewer = User.objects.create_user(email="viewer@test.com", password="pass")
        self.chat = Chat.objects.create(user=self.owner, title="Test", path="test")

    def test_create_viewer_grant(self):
        grant = ChatGrant.objects.create(
            chat=self.chat, user=self.viewer, role=ChatGrant.Role.VIEWER
        )
        self.assertEqual(grant.role, "viewer")
        self.assertEqual(str(grant), f"{self.viewer.id} (viewer) on {self.chat.id}")

    def test_create_editor_grant(self):
        grant = ChatGrant.objects.create(
            chat=self.chat, user=self.viewer, role=ChatGrant.Role.EDITOR
        )
        self.assertEqual(grant.role, "editor")

    def test_unique_chat_grant(self):
        ChatGrant.objects.create(
            chat=self.chat, user=self.viewer, role=ChatGrant.Role.VIEWER
        )
        with self.assertRaises(IntegrityError):
            ChatGrant.objects.create(
                chat=self.chat, user=self.viewer, role=ChatGrant.Role.EDITOR
            )

    def test_grants_accessible_from_chat(self):
        editor = User.objects.create_user(email="editor@test.com", password="pass")
        ChatGrant.objects.create(chat=self.chat, user=self.viewer, role=ChatGrant.Role.VIEWER)
        ChatGrant.objects.create(chat=self.chat, user=editor, role=ChatGrant.Role.EDITOR)
        self.assertEqual(self.chat.grants.count(), 2)


class ChatBranchTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="owner@test.com", password="pass")
        self.chat = Chat.objects.create(user=self.owner, title="Branch Test", path="branch")
        self.msg = Message.objects.create(
            chat=self.chat, role=Message.Role.USER, content="Original"
        )

    def test_create_branch(self):
        branch = ChatBranch.objects.create(
            chat=self.chat,
            name="feature-branch",
            base_message=self.msg,
        )
        self.assertEqual(branch.name, "feature-branch")
        self.assertEqual(branch.base_message, self.msg)
        self.assertTrue(branch.is_active)

    def test_unique_branch_name(self):
        ChatBranch.objects.create(chat=self.chat, name="main")
        with self.assertRaises(IntegrityError):
            ChatBranch.objects.create(chat=self.chat, name="main")

    def test_default_branch_name(self):
        branch = ChatBranch.objects.create(chat=self.chat)
        self.assertEqual(branch.name, "main")

    def test_set_head_message(self):
        head = Message.objects.create(
            chat=self.chat, role=Message.Role.ASSISTANT, content="Response"
        )
        branch = ChatBranch.objects.create(
            chat=self.chat, name="dev", base_message=self.msg, head_message=head
        )
        self.assertEqual(branch.head_message, head)


class TurnRunTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="owner@test.com", password="pass")
        self.chat = Chat.objects.create(user=self.owner, title="Turn Test", path="turn")
        self.msg = Message.objects.create(
            chat=self.chat, role=Message.Role.USER, content="Hello"
        )

    def test_create_turn_run(self):
        turn = TurnRun.objects.create(
            chat=self.chat,
            message=self.msg,
            status=TurnRun.Status.QUEUED,
        )
        self.assertEqual(turn.status, "queued")
        self.assertEqual(turn.message, self.msg)

    def test_turn_run_status_transition(self):
        turn = TurnRun.objects.create(chat=self.chat, message=self.msg)
        turn.status = TurnRun.Status.RUNNING
        turn.save()
        turn.refresh_from_db()
        self.assertEqual(turn.status, "running")

        turn.status = TurnRun.Status.COMPLETED
        turn.save()
        turn.refresh_from_db()
        self.assertEqual(turn.status, "completed")

    def test_turn_run_with_branch(self):
        branch = ChatBranch.objects.create(chat=self.chat, name="dev")
        turn = TurnRun.objects.create(
            chat=self.chat, message=self.msg, branch=branch
        )
        self.assertEqual(turn.branch, branch)

    def test_turn_run_with_worker_id(self):
        turn = TurnRun.objects.create(
            chat=self.chat,
            message=self.msg,
            worker_id="worker-1",
            status=TurnRun.Status.RUNNING,
        )
        self.assertEqual(turn.worker_id, "worker-1")

    def test_default_status_is_queued(self):
        turn = TurnRun.objects.create(chat=self.chat, message=self.msg)
        self.assertEqual(turn.status, TurnRun.Status.QUEUED)


class TurnEventTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="owner@test.com", password="pass")
        self.chat = Chat.objects.create(user=self.owner, title="Event Test", path="event")
        self.msg = Message.objects.create(
            chat=self.chat, role=Message.Role.USER, content="Hi"
        )
        self.turn = TurnRun.objects.create(chat=self.chat, message=self.msg)

    def test_create_progress_event(self):
        event = TurnEvent.objects.create(
            turn_run=self.turn,
            sequence=1,
            event_type=TurnEvent.EventType.PROGRESS,
            content="Extracting text...",
        )
        self.assertEqual(event.sequence, 1)
        self.assertEqual(event.event_type, "progress")

    def test_create_text_delta_event(self):
        event = TurnEvent.objects.create(
            turn_run=self.turn,
            sequence=2,
            event_type=TurnEvent.EventType.TEXT_DELTA,
            content="Hello",
        )
        self.assertEqual(event.event_type, "text_delta")

    def test_create_tool_call_event(self):
        event = TurnEvent.objects.create(
            turn_run=self.turn,
            sequence=3,
            event_type=TurnEvent.EventType.TOOL_CALL,
            metadata={"tool": "rag.search", "args": {"query": "test"}},
        )
        self.assertEqual(event.event_type, "tool_call")
        self.assertEqual(event.metadata["tool"], "rag.search")

    def test_create_done_event(self):
        event = TurnEvent.objects.create(
            turn_run=self.turn,
            sequence=4,
            event_type=TurnEvent.EventType.DONE,
        )
        self.assertEqual(event.event_type, "done")

    def test_unique_turn_event_sequence(self):
        TurnEvent.objects.create(
            turn_run=self.turn, sequence=1, event_type=TurnEvent.EventType.PROGRESS
        )
        with self.assertRaises(IntegrityError):
            TurnEvent.objects.create(
                turn_run=self.turn, sequence=1, event_type=TurnEvent.EventType.DONE
            )

    def test_events_ordered_by_sequence(self):
        TurnEvent.objects.create(
            turn_run=self.turn, sequence=2, event_type=TurnEvent.EventType.TEXT_DELTA
        )
        TurnEvent.objects.create(
            turn_run=self.turn, sequence=1, event_type=TurnEvent.EventType.PROGRESS
        )
        TurnEvent.objects.create(
            turn_run=self.turn, sequence=3, event_type=TurnEvent.EventType.DONE
        )
        events = TurnEvent.objects.filter(turn_run=self.turn).order_by("sequence")
        self.assertEqual([e.sequence for e in events], [1, 2, 3])

    def test_event_metadata_default(self):
        event = TurnEvent.objects.create(
            turn_run=self.turn, sequence=1, event_type=TurnEvent.EventType.PROGRESS
        )
        self.assertEqual(event.metadata, {})

    def test_cascade_delete_turn_run(self):
        TurnEvent.objects.create(
            turn_run=self.turn, sequence=1, event_type=TurnEvent.EventType.DONE
        )
        turn_id = self.turn.id
        self.turn.delete()
        self.assertFalse(TurnEvent.objects.filter(turn_run_id=turn_id).exists())


class MessageAuthorTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="user@test.com", password="pass")
        self.other = User.objects.create_user(email="other@test.com", password="pass")
        self.chat = Chat.objects.create(user=self.user, title="Author Test", path="author")

    def test_message_author_field(self):
        msg = Message.objects.create(
            chat=self.chat,
            author=self.user,
            role=Message.Role.USER,
            content="Hello from author",
        )
        self.assertEqual(msg.author, self.user)

    def test_message_author_nullable(self):
        msg = Message.objects.create(
            chat=self.chat,
            role=Message.Role.ASSISTANT,
            content="Auto response",
        )
        self.assertIsNone(msg.author)

    def test_messages_accessible_from_author(self):
        Message.objects.create(
            chat=self.chat, author=self.user, role=Message.Role.USER, content="Msg 1"
        )
        Message.objects.create(
            chat=self.chat, author=self.other, role=Message.Role.USER, content="Msg 2"
        )
        self.assertEqual(self.user.messages.count(), 1)


class MessageAttachmentDocumentReferenceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="user@test.com", password="pass")
        self.chat = Chat.objects.create(user=self.user, title="Attach Test", path="attach")
        self.msg = Message.objects.create(
            chat=self.chat, role=Message.Role.USER, content="See attached"
        )

    def test_create_attachment_without_document_reference(self):
        att = MessageAttachment.objects.create(
            message=self.msg,
            original_filename="test.txt",
            mime_type="text/plain",
            size_bytes=1024,
        )
        self.assertEqual(att.original_filename, "test.txt")
        self.assertIsNone(att.document_reference)

    def test_attachment_string_representation(self):
        att = MessageAttachment.objects.create(
            message=self.msg,
            original_filename="report.pdf",
        )
        self.assertEqual(str(att), "report.pdf")

    def test_attachment_default_mime_type(self):
        att = MessageAttachment.objects.create(
            message=self.msg,
            original_filename="unknown.bin",
        )
        self.assertEqual(att.mime_type, "")
