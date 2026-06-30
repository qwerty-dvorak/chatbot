from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import User


class RegistrationForm(UserCreationForm):
    email = forms.EmailField(required=True)
    name = forms.CharField(required=False, max_length=255)

    class Meta:
        model = User
        fields = ("email", "name", "password1", "password2")

    def clean_email(self):
        email = self.cleaned_data["email"]
        if User.objects.filter(email=email).exists():
            msg = "A user with this email already exists."
            raise forms.ValidationError(msg)
        return email
