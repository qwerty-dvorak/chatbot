from django import forms

from .models import Message


class MessageForm(forms.Form):
    content = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Type your message..."})
    )
    attachment = forms.FileField(required=False)

    def clean(self):
        cleaned = super().clean()
        content = cleaned.get("content")
        attachment = cleaned.get("attachment")
        if not content and not attachment:
            raise forms.ValidationError("Message content or attachment is required.")
        if attachment:
            if attachment.size > 50 * 1024 * 1024:
                raise forms.ValidationError("File size must be under 50MB.")
            allowed = [
                "text/plain", "text/markdown", "text/csv",
                "application/pdf",
                "image/png", "image/jpeg", "image/webp",
            ]
            if attachment.content_type not in allowed:
                raise forms.ValidationError(f"File type {attachment.content_type} is not supported.")
        return cleaned
