"""Environment configuration and safe production log formatting."""
import logging
import os
from pathlib import Path
from blackboard_auth import OAuthConfig


def database_path(default: Path) -> Path:
    value = os.getenv('DATABASE_PATH','').strip()
    path = Path(value).expanduser() if value else default
    if str(path) == ':memory:':
        raise ValueError('DATABASE_PATH must refer to persistent storage.')
    path.parent.mkdir(parents=True,exist_ok=True)
    return path


def admin_id() -> int | None:
    value = os.getenv('SYNC_ADMIN_USER_ID','').strip()
    if not value:
        return None
    if not value.isascii() or not value.isdecimal() or not 0<int(value)<2**64:
        raise ValueError('SYNC_ADMIN_USER_ID must be a valid Discord user ID.')
    return int(value)


def blackboard_config() -> OAuthConfig | None:
    names=('BLACKBOARD_BASE_URL','BLACKBOARD_CLIENT_ID','BLACKBOARD_CLIENT_SECRET','BLACKBOARD_REDIRECT_URI')
    values=[os.getenv(name,'').strip() for name in names]
    if not any(values):
        return None
    if not all(values):
        raise ValueError('Blackboard OAuth configuration is incomplete; set all four variables or leave all empty.')
    from blackboard_auth import BlackboardOAuth
    config=OAuthConfig(*values,offline=False)
    # Validate registration shape only; never authorize or contact a tenant.
    BlackboardOAuth(config,None)
    return config


class SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message=record.getMessage()
        for name in ('DISCORD_TOKEN','BLACKBOARD_CLIENT_SECRET'):
            value=os.getenv(name,'')
            if value:
                message=message.replace(value,'[REDACTED]')
        record.msg,record.args=message,()
        return True


def configure_logging() -> None:
    level=os.getenv('LOG_LEVEL','INFO').upper()
    if level not in ('INFO','WARNING','ERROR'):
        raise ValueError('LOG_LEVEL must be INFO, WARNING or ERROR.')
    logging.basicConfig(level=level,format='%(asctime)s level=%(levelname)s logger=%(name)s %(message)s')
    for handler in logging.getLogger().handlers:
        handler.addFilter(SecretFilter())
    logging.getLogger('discord.http').setLevel(logging.WARNING)
