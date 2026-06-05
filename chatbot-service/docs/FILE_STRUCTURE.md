# Proposed File Structure

This is the intended Django project structure. It is a design target, not currently created application code.

```text
chatbot/
  pyproject.toml
  uv.lock
  README.md
  manage.py
  Dockerfile
  docker-compose.yml
  .dockerignore

  config/
    __init__.py
    asgi.py
    settings/
      __init__.py
      base.py
      local.py
      production.py
      test.py
    urls.py
    wsgi.py

  apps/
    accounts/
      __init__.py
      admin.py
      apps.py
      forms.py
      managers.py
      models.py
      urls.py
      views.py
      migrations/
        __init__.py
      tests/
        __init__.py
        test_auth.py

    chat/
      __init__.py
      admin.py
      apps.py
      context.py
      forms.py
      models.py
      prompts.py
      streaming.py
      services.py
      urls.py
      views.py
      migrations/
        __init__.py
      tests/
        __init__.py
        test_chat_flow.py
        test_context_builder.py
        test_streaming.py

    memory/
      __init__.py
      admin.py
      apps.py
      models.py
      policies.py
      services.py
      migrations/
        __init__.py
      tests/
        __init__.py
        test_memory_retrieval.py

    knowledge/
      __init__.py
      admin.py
      apps.py
      hybrid_search.py
      models.py
      retrievers.py
      services.py
      tool_adapters.py
      migrations/
        __init__.py
      tests/
        __init__.py
        test_retrieval.py

    ingestion/
      __init__.py
      admin.py
      apps.py
      chunking.py
      extractors/
        __init__.py
        base.py
        image.py
        pdf.py
        text.py
      jobs.py
      models.py
      services.py
      management/
        __init__.py
        commands/
          __init__.py
          ingest_path.py
          retry_ingestion_jobs.py
          run_ingestion_worker.py
      migrations/
        __init__.py
      tests/
        __init__.py
        test_chunking.py
        test_ingestion_jobs.py

    llm/
      __init__.py
      apps.py
      clients.py
      embeddings.py
      errors.py
      prompts.py
      streaming.py
      token_usage.py
      tests/
        __init__.py
        test_litellm_client.py

    tools/
      __init__.py
      admin.py
      apps.py
      builtin.py
      executor.py
      models.py
      permissions.py
      registry.py
      schemas.py
      migrations/
        __init__.py
      tests/
        __init__.py
        test_tool_permissions.py
        test_tool_records.py

    compaction/
      __init__.py
      apps.py
      models.py
      services.py
      tests/
        __init__.py
        test_compaction.py

  templates/
    base.html
    accounts/
      login.html
      register.html
      password_reset.html
    chat/
      chat_detail.html
      chat_list.html
      chat_new.html
    knowledge/
      document_detail.html
      document_list.html
      upload.html

  static/
    css/
      app.css

  media/
    uploads/

  docs/
    README.md
    ARCHITECTURE.md
    FILE_STRUCTURE.md
    DATA_MODEL.md
    RAG_MULTIMODAL_PIPELINE.md
    API_AND_VIEWS.md
    CONFIGURATION_AND_OPERATIONS.md
    OPEN_QUESTIONS.md
    DETAILED_DB_SCHEMA.md
```

## Notes

- `static/css/app.css` is plain CSS only.
- No npm build step is required.
- Any browser JavaScript must be hand-written and small, mainly for streaming display.
- `templates/` should stay server-rendered.
- `apps/llm` should isolate LiteLLM usage so model providers can change without touching chat and ingestion logic.
- `apps/tools` should own tool schemas, permission checks, execution, and durable audit records.
- `apps/knowledge` should own retrieval; `apps/ingestion` should own extraction and chunk creation.
- Docker Compose should run web, worker, PostgreSQL, and optional model-facing services.
- Management commands are still useful for local batch ingestion and reprocessing.
