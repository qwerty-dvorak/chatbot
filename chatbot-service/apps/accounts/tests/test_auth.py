from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import SecurityLog, User


class UserModelTest(TestCase):
    def test_create_user_with_email(self):
        user = User.objects.create_user(email="test@example.com", password="pass123")
        self.assertEqual(user.email, "test@example.com")
        self.assertTrue(user.check_password("pass123"))
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_create_user_without_email_raises(self):
        with self.assertRaises(ValueError):
            User.objects.create_user(email="", password="pass123")

    def test_create_superuser(self):
        user = User.objects.create_superuser(email="admin@example.com", password="admin123")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_active)

    def test_email_normalization(self):
        email = "Test@Example.COM"
        user = User.objects.create_user(email=email, password="pass123")
        self.assertEqual(user.email, "Test@example.com")

    def test_user_str(self):
        user = User.objects.create_user(email="test@example.com", password="pass123")
        self.assertEqual(str(user), "test@example.com")

    def test_user_default_preferences(self):
        user = User.objects.create_user(email="test@example.com", password="pass123")
        self.assertEqual(user.preferences, {})


class SecurityLogModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")

    def test_create_security_log(self):
        log = SecurityLog.objects.create(
            user=self.user,
            event="login_success",
            method="password",
            ip_address="127.0.0.1",
        )
        self.assertEqual(log.event, "login_success")
        self.assertEqual(log.method, "password")
        self.assertEqual(log.ip_address, "127.0.0.1")
        self.assertIsNotNone(log.created_at)

    def test_anonymous_security_log(self):
        log = SecurityLog.objects.create(
            event="login_failed",
            method="password",
            ip_address="192.168.1.1",
            metadata={"attempted_email": "unknown@example.com"},
        )
        self.assertIsNone(log.user)
        self.assertEqual(log.metadata["attempted_email"], "unknown@example.com")

    def test_security_log_str(self):
        log = SecurityLog.objects.create(
            user=self.user, event="login_success", method="password"
        )
        self.assertIn("login_success", str(log))


class RegistrationViewTest(TestCase):
    def test_registration_page_loads(self):
        response = self.client.get(reverse("accounts:register"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/register.html")

    def test_registration_creates_user_and_logs_in(self):
        data = {
            "email": "newuser@example.com",
            "password1": "secure_password_123",
            "password2": "secure_password_123",
        }
        self.client.post(reverse("accounts:register"), data, follow=True)
        self.assertTrue(User.objects.filter(email="newuser@example.com").exists())
        user = User.objects.get(email="newuser@example.com")
        self.assertIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.session["_auth_user_id"], str(user.id))

    def test_registration_creates_security_log(self):
        data = {
            "email": "logtest@example.com",
            "password1": "secure_password_123",
            "password2": "secure_password_123",
        }
        self.client.post(reverse("accounts:register"), data)
        self.assertTrue(
            SecurityLog.objects.filter(
                event="registration", user__email="logtest@example.com"
            ).exists()
        )

    def test_registration_duplicate_email(self):
        User.objects.create_user(email="dup@example.com", password="pass123")
        data = {
            "email": "dup@example.com",
            "password1": "secure_password_123",
            "password2": "secure_password_123",
        }
        response = self.client.post(reverse("accounts:register"), data)
        self.assertEqual(response.status_code, 200)
        form = response.context.get("form")
        self.assertIsNotNone(form)
        self.assertIn("email", form.errors)
        self.assertIn("A user with this email already exists.", form.errors["email"])

    def test_registration_password_mismatch(self):
        data = {
            "email": "mismatch@example.com",
            "password1": "password123",
            "password2": "different_password",
        }
        response = self.client.post(reverse("accounts:register"), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="mismatch@example.com").exists())


class LoginViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")

    def test_login_page_loads(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/login.html")

    def test_login_with_valid_credentials(self):
        response = self.client.post(
            reverse("login"), {"username": "test@example.com", "password": "pass123"}, follow=True
        )
        self.assertIn("_auth_user_id", self.client.session)

    def test_login_with_invalid_credentials(self):
        response = self.client.post(
            reverse("login"), {"username": "test@example.com", "password": "wrong"}, follow=True
        )
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertContains(response, "Please enter a correct email and password.")

    def test_logout(self):
        self.client.login(username="test@example.com", password="pass123")
        response = self.client.post(reverse("logout"), follow=True)
        self.assertNotIn("_auth_user_id", self.client.session)
