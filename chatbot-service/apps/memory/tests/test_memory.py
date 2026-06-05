from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.memory.models import Memory, MemorySettings
from apps.memory.services import get_memory_settings, get_user_memories, save_memory


class MemoryModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")

    def test_create_memory(self):
        memory = Memory.objects.create(user=self.user, content="User likes Python", importance=3)
        self.assertEqual(memory.content, "User likes Python")
        self.assertEqual(memory.importance, 3)

    def test_memory_default_importance(self):
        memory = Memory.objects.create(user=self.user, content="Default importance")
        self.assertEqual(memory.importance, 1)

    def test_memory_str(self):
        memory = Memory.objects.create(user=self.user, content="Short memory")
        self.assertEqual(str(memory), "Short memory")


class MemorySettingsModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")

    def test_create_settings(self):
        settings = MemorySettings.objects.create(user=self.user)
        self.assertTrue(settings.is_enabled)
        self.assertTrue(settings.auto_save)
        self.assertEqual(settings.max_tokens, 2000)
        self.assertEqual(settings.auto_save_filter, "medium")

    def test_settings_one_to_one(self):
        MemorySettings.objects.create(user=self.user)
        with self.assertRaises(Exception):
            MemorySettings.objects.create(user=self.user)


class MemoryServicesTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        Memory.objects.create(user=self.user, content="Likes Django", importance=3)
        Memory.objects.create(user=self.user, content="Prefers PostgreSQL", importance=2)

    def test_get_user_memories(self):
        memories = get_user_memories(self.user)
        self.assertEqual(len(memories), 2)

    def test_get_user_memories_with_query(self):
        memories = get_user_memories(self.user, query="Django")
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0].content, "Likes Django")

    def test_save_memory(self):
        memory = save_memory(self.user, "New memory", importance=5)
        self.assertEqual(memory.content, "New memory")
        self.assertEqual(memory.importance, 5)

    def test_get_memory_settings_creates_default(self):
        settings = get_memory_settings(self.user)
        self.assertIsNotNone(settings)
        self.assertTrue(settings.is_enabled)

    def test_get_memory_settings_returns_existing(self):
        MemorySettings.objects.create(user=self.user, is_enabled=False)
        settings = get_memory_settings(self.user)
        self.assertFalse(settings.is_enabled)


class MemoryListViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.client.login(username="test@example.com", password="pass123")
        Memory.objects.create(user=self.user, content="Visible memory", importance=2)

    def test_list_shows_user_memories(self):
        response = self.client.get(reverse("memory:list"))
        self.assertContains(response, "Visible memory")

    def test_list_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("memory:list"))
        self.assertEqual(response.status_code, 302)


class MemorySettingsViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.client.login(username="test@example.com", password="pass123")

    def test_settings_page_loads(self):
        response = self.client.get(reverse("memory:settings"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "memory/memory_settings.html")

    def test_settings_update(self):
        response = self.client.post(reverse("memory:settings"), {
            "is_enabled": False,
            "max_tokens": 1000,
            "auto_save": False,
            "auto_save_filter": "high",
            "allow_tool_updates": False,
        }, follow=True)
        settings = MemorySettings.objects.get(user=self.user)
        self.assertFalse(settings.is_enabled)
        self.assertEqual(settings.max_tokens, 1000)
