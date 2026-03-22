def handle_status() -> str:
    return "ok"


def handle_reclaim() -> str:
    return "reclaimed"


def run(command: str) -> str:
    handlers = {
        "status": handle_status,
        "reclaim": handle_reclaim,
    }
    return handlers[command]()
