from enum import IntEnum, auto


class ProductState(IntEnum):
    PHOTO = auto()
    DATA = auto()           # receive all fields in one multiline message
    CATEGORY_PICK = auto()  # shown when category is not in allowed list


class CatalogEditState(IntEnum):
    PHOTO = auto()
    DATA = auto()
    CATEGORY_PICK = auto()


class FactoryPanelState(IntEnum):
    IDLE = auto()
    ADDING = auto()


class ExhibitionPanelState(IntEnum):
    IDLE = auto()
    ADDING = auto()       # waiting for exhibition name
    ADDING_YEAR = auto()  # waiting for exhibition year


class SettingsPanelState(IntEnum):
    IDLE = auto()
    EDITING = auto()      # waiting for new numeric value
