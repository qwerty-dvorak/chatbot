import logging
import time

logger = logging.getLogger("django.request")


class RequestLoggingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.perf_counter()

        response = self.get_response(request)

        duration = time.perf_counter() - start
        extra = {
            "method": request.method,
            "path": request.path,
            "status": response.status_code,
            "duration_ms": round(duration * 1000, 1),
        }
        if request.user.is_authenticated:
            extra["user"] = str(request.user)

        log_fn = logger.warning if response.status_code >= 400 else logger.info
        log_fn(
            "[%.0fms] %s %s → %s%s",
            extra["duration_ms"],
            extra["method"],
            extra["path"],
            extra["status"],
            f" ({extra['user']})" if "user" in extra else "",
            extra=extra,
        )

        return response
