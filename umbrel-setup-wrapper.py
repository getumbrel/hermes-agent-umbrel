#!/usr/bin/env python3
"""Umbrel-specific first-run wrapper around the upstream Hermes setup wizard."""

from types import SimpleNamespace


SETUP_MODE_DESCRIPTION = (
    "Hermes needs an AI model to work.\n"
    "Quick Setup requires a Nous Portal account.\n"
    "Full setup connects a service you already use, like OpenAI."
)


def main() -> None:
    from hermes_cli import setup as setup_mod

    upstream_prompt_choice = setup_mod.prompt_choice

    def umbrel_prompt_choice(question, choices, default=0, description=None):
        if (
            question == "How would you like to set up Hermes?"
            and len(choices) == 2
            and "Quick Setup" in str(choices[0])
            and "Full setup" in str(choices[1])
        ):
            # Keep upstream's choices/default intact, but provide short explicit
            # lines because the dashboard PTY truncates long curses descriptions.
            return upstream_prompt_choice(
                question,
                choices,
                default,
                description=SETUP_MODE_DESCRIPTION,
            )

        return upstream_prompt_choice(question, choices, default, description=description)

    setup_mod.prompt_choice = umbrel_prompt_choice
    setup_mod.run_setup_wizard(
        SimpleNamespace(
            section=None,
            non_interactive=False,
            reset=False,
            reconfigure=False,
            quick=False,
            portal=False,
        )
    )


if __name__ == "__main__":
    main()
