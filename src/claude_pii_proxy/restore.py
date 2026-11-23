"""Inbound restoration (contracts/placeholder-format.md, Inbound)."""

from claude_pii_proxy.vault import TOKEN_PATTERN, Vault


def restore_text(text: str, vault: Vault) -> str:
    """Replace issued tokens (any case) by their values; leave anything else as it is."""
    return TOKEN_PATTERN.sub(lambda m: vault.value_for(m.group()) or m.group(), text)
