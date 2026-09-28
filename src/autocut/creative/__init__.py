"""创意生成层：文案与配乐。"""

from .copywriter import CopyTemplate, Copywriter, extract_variables, load_templates
from .music import MusicLibrary, MusicSelector, resolve_music_path

__all__ = [
    "Copywriter",
    "CopyTemplate",
    "load_templates",
    "extract_variables",
    "MusicSelector",
    "MusicLibrary",
    "resolve_music_path",
]
