from django.core.management.base import BaseCommand

from apps.tools.models import ToolDefinition


class Command(BaseCommand):
    help = "List all registered tools"

    def handle(self, *args, **options):
        tools = ToolDefinition.objects.all().order_by("name")
        for tool in tools:
            status = "enabled" if tool.is_enabled else "disabled"
            self.stdout.write(f"  {tool.name:30s} v{tool.version:5s} [{status}]  {tool.display_name}")
        self.stdout.write(self.style.SUCCESS(f"\n{tools.count()} tools total"))
