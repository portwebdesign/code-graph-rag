def handle_status() -> str:
    return "ok"


def build_handlers():
    return {
        "status": handle_status,
    }


def run(command: str) -> str:
    handlers = build_handlers()
    return handlers[command]()
