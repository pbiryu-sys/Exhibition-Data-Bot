from enum import IntEnum, auto


class ProductState(IntEnum):
    PHOTO = auto()
    CATEGORY = auto()
    ARTICLE = auto()
    PRICE = auto()
    QTY_40HQ = auto()
    NOTE = auto()


class SettingsState(IntEnum):
    CHOOSING = auto()
    EDITING = auto()


class ContextState(IntEnum):
    FACTORY_CHOOSE = auto()
    FACTORY_CUSTOM = auto()
    EXHIBITION_CHOOSE = auto()
    EXHIBITION_CUSTOM = auto()


class FactoryMgmtState(IntEnum):
    MENU = auto()
    ADD_NAME = auto()
    DELETE_CHOOSE = auto()


class ExhibitionMgmtState(IntEnum):
    MENU = auto()
    ADD_NAME = auto()
    ADD_YEAR = auto()
    DELETE_CHOOSE = auto()
