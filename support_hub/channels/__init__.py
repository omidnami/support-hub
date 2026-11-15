from .bale import Bale
from .telegram import Telegram
from .rubika import Rubika
from .eitaa import Eitaa

from .instagram import Instagram

ADAPTERS = {cls.platform: cls for cls in (Bale, Telegram, Rubika, Eitaa, Instagram)}
