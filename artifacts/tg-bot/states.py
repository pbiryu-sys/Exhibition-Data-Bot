from enum import IntEnum, auto


class ProductState(IntEnum):
    PHOTO         = auto()
    DATA          = auto()
    CATEGORY_PICK = auto()


class EditPanelState(IntEnum):
    IDLE          = auto()
    ADDING_PHOTO  = auto()
    ADDING_VIDEO  = auto()
    ADDING_NOTE   = auto()
    EDITING_CAT   = auto()
    EDITING_PRICE = auto()
    EDITING_FACT  = auto()
    EDITING_EXH   = auto()
    EDITING_SKU   = auto()
    ADDING_DOC    = auto()


class FactoryPanelState(IntEnum):
    IDLE   = auto()
    ADDING = auto()


class ExhibitionPanelState(IntEnum):
    IDLE        = auto()
    ADDING      = auto()
    ADDING_YEAR = auto()


class SettingsPanelState(IntEnum):
    IDLE    = auto()
    EDITING = auto()


class AdminPanelState(IntEnum):
    IDLE         = auto()
    ADDING_USER  = auto()
    REMOVING_USER = auto()


class CategoryPanelState(IntEnum):
    IDLE   = auto()
    ADDING = auto()


class CatalogState(IntEnum):
    MENU           = auto()
    FACTORY_LIST   = auto()
    CATEGORY_LIST  = auto()
    SEARCH         = auto()
    LIST           = auto()
    DETAIL         = auto()
    DELETE_CONFIRM = auto()
