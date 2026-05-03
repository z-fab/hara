"""HARA CLI entrypoint — Typer app.

Single Typer app with subcommands. `load_dotenv()` is called at import time
so that any subcommand's logic sees env vars (some libs read them on import).
"""

from __future__ import annotations

from dotenv import load_dotenv

# Load .env before anything else imports settings.
load_dotenv()

import typer  # noqa: E402

from hara.cli.commands.chat import register as register_chat  # noqa: E402
from hara.cli.commands.doctor import doctor_command  # noqa: E402
from hara.cli.commands.ingest import register as register_ingest  # noqa: E402
from hara.cli.commands.init import register as register_init  # noqa: E402
from hara.cli.commands.lists import connectors_app, providers_app  # noqa: E402
from hara.cli.commands.semantic_map import register as register_semantic_map  # noqa: E402
from hara.cli.commands.serve import register as register_serve  # noqa: E402
from hara.cli.commands.version import version_command  # noqa: E402

app = typer.Typer(
    name="hara",
    help=(
        "Hybrid Agent for Retrieval and Answering — open source multi-agent "
        "for hybrid SQL+document Q&A"
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
)

app.command(name="version")(version_command)
app.add_typer(connectors_app, name="connectors")
app.add_typer(providers_app, name="providers")
app.command(name="doctor")(doctor_command)
register_ingest(app)
register_semantic_map(app)
register_chat(app)
register_init(app)
register_serve(app)
