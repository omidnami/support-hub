from .bale import Bale
from .telegram import Telegram
from .rubika import Rubika
from .eitaa import Eitaa

ADAPTERS = {cls.platform: cls for cls in (Bale, Telegram, Rubika, Eitaa)}
