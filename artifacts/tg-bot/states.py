from enum import IntEnum, auto


class ProductState(IntEnum):
    PHOTO = auto()
    FACTORY = auto()
    CATEGORY = auto()
    ARTICLE = auto()
    PRICE = auto()
    QTY_40HQ = auto()
    NOTE = auto()


class SettingsState(IntEnum):
    CHOOSING = auto()
    EDITING = auto()
