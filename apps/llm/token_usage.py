from .models import TokenUsage


def record_token_usage(
    operation: str,
    model: str,
    provider: str = "litellm",
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
    user=None,
    chat=None,
    message=None,
    tool_call=None,
    request_id=None,
    step_index=None,
    metadata=None,
):
    return TokenUsage.objects.create(
        user=user,
        chat=chat,
        message=message,
        tool_call=tool_call,
        operation=operation,
        provider=provider,
        model=model,
        request_id=request_id,
        step_index=step_index,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        metadata=metadata or {},
    )
