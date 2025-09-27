from config import PRIVATE_CHANNEL_ID

def should_be_ephemeral(ctx) -> bool:
    ch = getattr(ctx, "channel", None)
    return bool(ch and ch.id == PRIVATE_CHANNEL_ID)
