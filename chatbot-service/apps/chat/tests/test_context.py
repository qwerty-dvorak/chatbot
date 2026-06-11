import json
import struct
import zlib

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.chat.context import ContextBuilder, _build_multimodal_content
from apps.chat.models import Chat, Message


def _make_minimal_png(width: int = 1, height: int = 1) -> bytes:
    """Create a minimal valid PNG file."""
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


class MultimodalContentTest(TestCase):
    def test_text_only_no_attachments(self):
        result = _build_multimodal_content("Hello", [])
        self.assertEqual(result, "Hello")

    def test_text_only_none_attachments(self):
        result = _build_multimodal_content("Hello", None)
        self.assertEqual(result, "Hello")

    def test_image_attachment_creates_image_url_part(self):
        png_data = _make_minimal_png()
        path = default_storage.save("test_attachments/test_image.png", ContentFile(png_data))
        try:
            attachments = [{
                "file": path,
                "original_filename": "test_image.png",
                "mime_type": "image/png",
                "size_bytes": len(png_data),
                "sha256": "abc123",
            }]
            result = _build_multimodal_content("Describe this", attachments)
            self.assertIsInstance(result, list)
            self.assertEqual(len(result), 2)
            self.assertEqual(result[0]["type"], "text")
            self.assertEqual(result[0]["text"], "Describe this")
            self.assertEqual(result[1]["type"], "image_url")
            self.assertTrue(result[1]["image_url"]["url"].startswith("data:image/png;base64,"))
        finally:
            default_storage.delete(path)

    def test_image_without_text(self):
        png_data = _make_minimal_png()
        path = default_storage.save("test_attachments/img_only.png", ContentFile(png_data))
        try:
            attachments = [{
                "file": path,
                "original_filename": "img_only.png",
                "mime_type": "image/png",
                "size_bytes": len(png_data),
                "sha256": "abc",
            }]
            result = _build_multimodal_content("", attachments)
            self.assertIsInstance(result, list)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["type"], "image_url")
        finally:
            default_storage.delete(path)

    def test_text_file_attachment_injects_content(self):
        file_content = "Hello from the text file!"
        path = default_storage.save("test_attachments/test.txt", ContentFile(file_content))
        try:
            attachments = [{
                "file": path,
                "original_filename": "test.txt",
                "mime_type": "text/plain",
                "size_bytes": len(file_content.encode()),
                "sha256": "def456",
            }]
            result = _build_multimodal_content("Read this", attachments)
            self.assertIsInstance(result, list)
            text_parts = [p["text"] for p in result if p["type"] == "text"]
            combined = " ".join(text_parts)
            self.assertIn("Hello from the text file!", combined)
            self.assertIn("Read this", combined)
        finally:
            default_storage.delete(path)

    def test_unknown_mime_type_attachment(self):
        attachments = [{
            "file": "/nonexistent/foo.bin",
            "original_filename": "foo.bin",
            "mime_type": "application/octet-stream",
            "size_bytes": 42,
            "sha256": "ghi789",
        }]
        result = _build_multimodal_content("What is this?", attachments)
        self.assertIsInstance(result, list)
        text_parts = [p["text"] for p in result if p["type"] == "text"]
        combined = " ".join(text_parts)
        self.assertIn("foo.bin", combined)
        self.assertIn("application/octet-stream", combined)

    def test_multiple_attachments(self):
        png_data = _make_minimal_png()
        img_path = default_storage.save("test_attachments/multi_img.png", ContentFile(png_data))
        txt_path = default_storage.save("test_attachments/multi.txt", ContentFile("Some text"))
        try:
            attachments = [
                {"file": img_path, "original_filename": "multi_img.png", "mime_type": "image/png", "size_bytes": len(png_data), "sha256": "a"},
                {"file": txt_path, "original_filename": "multi.txt", "mime_type": "text/plain", "size_bytes": 9, "sha256": "b"},
            ]
            result = _build_multimodal_content("Analyze", attachments)
            self.assertIsInstance(result, list)
            self.assertGreaterEqual(len(result), 3)
            image_parts = [p for p in result if p.get("type") == "image_url"]
            self.assertEqual(len(image_parts), 1)
        finally:
            default_storage.delete(img_path)
            default_storage.delete(txt_path)

    def test_missing_image_file_fallback(self):
        attachments = [{
            "file": "uploads/nonexistent.png",
            "original_filename": "missing.png",
            "mime_type": "image/png",
            "size_bytes": 100,
            "sha256": "x",
        }]
        result = _build_multimodal_content("See this?", attachments)
        self.assertIsInstance(result, list)
        text_parts = [p["text"] for p in result if p["type"] == "text"]
        combined = " ".join(text_parts)
        self.assertIn("missing.png", combined)
        self.assertIn("not available", combined)


class ContextBuilderMultimodalTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass")
        self.chat = Chat.objects.create(user=self.user, title="Multi", path="mm1")

    def test_build_with_image_attachment(self):
        png_data = _make_minimal_png()
        path = default_storage.save("test_ctx/img.png", ContentFile(png_data))
        try:
            msg = Message.objects.create(
                chat=self.chat, role=Message.Role.USER,
                content="What is in this image?",
                attachments=[{
                    "file": path,
                    "original_filename": "img.png",
                    "mime_type": "image/png",
                    "size_bytes": len(png_data),
                    "sha256": "aaa",
                }],
            )
            builder = ContextBuilder(self.chat, self.user)
            result = builder.build("What is in this image?", msg)
            last = result[-1]
            self.assertEqual(last["role"], "user")
            self.assertIsInstance(last["content"], list)
            types = [p["type"] for p in last["content"]]
            self.assertIn("image_url", types)
            self.assertIn("text", types)
        finally:
            default_storage.delete(path)

    def test_history_with_attachment(self):
        png_data = _make_minimal_png()
        path = default_storage.save("test_ctx/history_img.png", ContentFile(png_data))
        try:
            hist_msg = Message.objects.create(
                chat=self.chat, role=Message.Role.USER,
                content="Previous image",
                attachments=[{
                    "file": path,
                    "original_filename": "history_img.png",
                    "mime_type": "image/png",
                    "size_bytes": len(png_data),
                    "sha256": "bbb",
                }],
                status=Message.Status.COMPLETED,
            )

            builder = ContextBuilder(self.chat, self.user)
            result = builder.build("Follow up", None)
            history_msgs = [m for m in result if m["role"] == "user"]
            self.assertGreaterEqual(len(history_msgs), 1)
            hist_content = history_msgs[0]["content"]
            if isinstance(hist_content, list):
                types = [p["type"] for p in hist_content]
                self.assertIn("image_url", types)
            else:
                self.assertEqual(hist_content, "Previous image")
        finally:
            default_storage.delete(path)


class ChatAPIAttachmentTest(TestCase):
    def setUp(self):
        self.password = "testpass123"
        self.user = User.objects.create_user(
            email="attachment_test@example.com", password=self.password,
        )
        self.client.login(username=self.user.email, password=self.password)

    def test_send_message_with_image_attachment(self):
        png_data = _make_minimal_png()
        image = ContentFile(png_data, name="test_upload.png")

        chat = Chat.objects.create(user=self.user, title="Attach", path="att1")
        response = self.client.post(
            reverse("chat:detail", args=[chat.id]),
            {"content": "What is this?", "attachment": image},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        msg = Message.objects.filter(
            chat=chat, role=Message.Role.USER, status=Message.Status.COMPLETED
        ).first()
        self.assertIsNotNone(msg)
        self.assertEqual(msg.content, "What is this?")
        self.assertTrue(len(msg.attachments) > 0)
        att = msg.attachments[0]
        self.assertEqual(att["original_filename"], "test_upload.png")
        self.assertEqual(att["mime_type"], "image/png")
        self.assertIn("sha256", att)
        self.assertGreater(att["size_bytes"], 0)

    def test_send_message_with_text_file_attachment(self):
        file_content = b"Hello, this is a test document."
        text_file = ContentFile(file_content, name="test_doc.txt")

        chat = Chat.objects.create(user=self.user, title="DocAttach", path="doc1")
        response = self.client.post(
            reverse("chat:detail", args=[chat.id]),
            {"content": "Read this file", "attachment": text_file},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        msg = Message.objects.filter(
            chat=chat, role=Message.Role.USER, status=Message.Status.COMPLETED
        ).first()
        self.assertIsNotNone(msg)
        self.assertEqual(msg.attachments[0]["original_filename"], "test_doc.txt")
        self.assertEqual(msg.attachments[0]["mime_type"], "text/plain")

    def test_stream_with_image_stores_attachment_metadata(self):
        png_data = _make_minimal_png()
        image = ContentFile(png_data, name="stream_img.png")

        chat = Chat.objects.create(user=self.user, title="StreamImg", path="si1")
        self.client.post(
            reverse("chat:detail", args=[chat.id]),
            {"content": "Describe this image", "attachment": image},
            follow=True,
        )
        user_msg = Message.objects.filter(
            chat=chat, role=Message.Role.USER
        ).first()
        self.assertIsNotNone(user_msg)
        self.assertEqual(user_msg.attachments[0]["mime_type"], "image/png")

        pending = Message.objects.filter(
            chat=chat, role=Message.Role.ASSISTANT, status=Message.Status.PENDING
        ).first()
        self.assertIsNotNone(pending)
