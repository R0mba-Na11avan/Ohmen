"""Script PyInstaller freezes into the sidecar executable (see sidecar.spec)."""
import sys

if sys.argv[1:] == ["--selftest"]:
    # CI runs this on the frozen build: imports that only happen mid-chat
    # (model SDKs, reply pricing) must work before an installer ships.
    import importlib

    for mod in (
        "pcb_assistant.server.app",
        "pydantic_ai.models.anthropic", "pydantic_ai.models.google", "pydantic_ai.models.openai",
        "pydantic_ai.providers.anthropic", "pydantic_ai.providers.google", "pydantic_ai.providers.openai",
        "pydantic_ai._genai_prices", "genai_prices",
    ):
        importlib.import_module(mod)
    from genai_prices import Usage, calc_price

    calc_price(Usage(input_tokens=10, output_tokens=10), model_ref="gpt-4o", provider_id="openai")
    print("selftest ok")
    sys.exit(0)

from pcb_assistant.server.__main__ import main

main()
