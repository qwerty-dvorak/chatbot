from django import forms

from .models import MemorySettings


class MemorySettingsForm(forms.ModelForm):
    class Meta:
        model = MemorySettings
        fields = ["is_enabled", "max_tokens", "auto_save", "auto_save_filter", "allow_tool_updates"]
