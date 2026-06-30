from django import forms


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
    knowledge_indices = forms.CharField(required=False)

    def clean(self):
        cleaned = super().clean()
        content = cleaned.get("content")
        attachments = cleaned.get("attachment") or []
        if not content and not attachments:
            msg = "Message content or attachment is required."
            raise forms.ValidationError(msg)
        max_attachment_size = 50 * 1024 * 1024
        for attachment in attachments:
            if attachment.size > max_attachment_size:
                msg = "File size must be under 50MB."
                raise forms.ValidationError(msg)
            allowed = [
                "text/plain", "text/markdown", "text/csv",
                "application/pdf",
                "image/png", "image/jpeg", "image/webp",
            ]
            if attachment.content_type not in allowed:
                msg = f"File type {attachment.content_type} is not supported."
                raise forms.ValidationError(msg)
        max_attachments = 10
        if len(attachments) > max_attachments:
            msg = "A message can contain at most 10 attachments."
            raise forms.ValidationError(msg)
        return cleaned
