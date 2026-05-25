from django.core.management.base import BaseCommand

from apps.knowledge.retrievers import hybrid_search


class Command(BaseCommand):
    help = "Search the knowledge base from the command line"

    def add_arguments(self, parser):
        parser.add_argument("query", type=str, help="Search query")
        parser.add_argument("--top-k", type=int, default=8, help="Number of results")

    def handle(self, *args, **options):
        query = options["query"]
        top_k = options["top_k"]
        results = hybrid_search(query, None, top_k=top_k)
        self.stdout.write(f"Found {len(results)} results for: {query}")
        for hit in results:
            self.stdout.write(f"  #{hit.rank} ({hit.score:.3f}) - {hit.source_title}")
