from django.core.management.base import BaseCommand

from apps.tools.models import ToolDefinition

BUILTIN_TOOL_DEFS = [
    {
        "name": "rag.search",
        "display_name": "RAG Search",
        "description": "Search accessible knowledge chunks for relevant information",
        "schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "top_k": {"type": "integer", "description": "Number of results", "default": 8},
            },
            "required": ["query"],
        },
    },
    {
        "name": "knowledge.ingest_status",
        "display_name": "Ingest Status",
        "description": "Check document ingestion state",
        "schema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "description": "Document ID to check"},
            },
            "required": [],
        },
    },
    {
        "name": "memory.search",
        "display_name": "Memory Search",
        "description": "Retrieve current user's memories",
        "schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query for memories"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory.save",
        "display_name": "Memory Save",
        "description": "Save or update memory under user settings",
        "schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Memory content to save"},
                "importance": {"type": "integer", "description": "Importance 1-5", "default": 1},
            },
            "required": ["content"],
        },
    },
    {
        "name": "chat.compact",
        "display_name": "Chat Compaction",
        "description": "Compact older chat history to save context",
        "schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "document.analyze",
        "display_name": "Document Analysis",
        "description": "Analyze an uploaded document or asset",
        "schema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "description": "Document ID to analyze"},
            },
            "required": ["document_id"],
        },
    },
]


class Command(BaseCommand):
    help = "Sync built-in tool definitions from code to database"

    def handle(self, *args, **options):
        created = 0
        updated = 0
        for def_data in BUILTIN_TOOL_DEFS:
            tool, was_created = ToolDefinition.objects.update_or_create(
                name=def_data["name"],
                defaults={
                    "display_name": def_data["display_name"],
                    "description": def_data["description"],
                    "schema": def_data["schema"],
                    "is_builtin": True,
                    "is_enabled": True,
                },
            )
            if was_created:
                created += 1
                self.stdout.write(f"Created: {tool.name}")
            else:
                updated += 1
                self.stdout.write(f"Updated: {tool.name}")

        self.stdout.write(self.style.SUCCESS(f"Done. {created} created, {updated} updated."))
