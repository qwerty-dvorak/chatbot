from django import forms

from .models import Message


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def clean(self, data, initial=None):
        clean_one = super().clean
        return [clean_one(item, initial) for item in (data if isinstance(data, (list, tuple)) else [data])]


class MessageForm(forms.Form):
    content = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Type your message..."})
    )
    attachment = MultipleFileField(required=False, widget=MultipleFileInput)
    thinking_mode = forms.BooleanField(required=False)

    def clean(self):
        cleaned = super().clean()
        content = cleaned.get("content")
        attachments = cleaned.get("attachment") or []
        if not content and not attachments:
            raise forms.ValidationError("Message content or attachment is required.")
        for attachment in attachments:
            if attachment.size > 50 * 1024 * 1024:
                raise forms.ValidationError("File size must be under 50MB.")
            allowed = [
                "text/plain", "text/markdown", "text/csv",
                "application/pdf",
                "image/png", "image/jpeg", "image/webp",
            ]
            if attachment.content_type not in allowed:
                raise forms.ValidationError(f"File type {attachment.content_type} is not supported.")
        if len(attachments) > 10:
            raise forms.ValidationError("A message can contain at most 10 attachments.")
        return cleaned
