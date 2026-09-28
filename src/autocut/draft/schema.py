"""剪映专业版草稿 schema 常量与基础工厂。

草稿工程由两个文件构成：
    <草稿目录>/draft_content.json     时间线主体（素材、轨道、片段）
    <草稿目录>/draft_meta_info.json   元信息（草稿名、路径、素材清单）

版本适配：剪映升级会改动 schema 细节，因此这里把「版本相关」的字段集中成 profile，
生成器只依赖 profile，不硬编码版本号。当前 profile 对应剪映专业版 5.9 系（version=360000）。
校准方式见 docs 与 README 的「草稿 schema 校准」章节。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

TIME_SCALE = 1_000_000      # 剪映内部时间单位：微秒
TEXT_SCALE = 1_000_000


def new_id() -> str:
    """剪映内部 id 形态：32 位大写十六进制（无连字符）。"""
    return uuid.uuid4().hex.upper()


def sec_to_us(value: float) -> int:
    return int(round(value * TIME_SCALE))


def us_to_sec(value: int) -> float:
    return round(value / TIME_SCALE, 6)


@dataclass
class SchemaProfile:
    """版本相关字段集合。"""

    key: str = "jianying_5_9"
    version: int = 360000
    new_version: str = "110.0.0"
    app_version: str = "5.9.0"
    app_id: int = 3704
    app_source: str = "lv"
    os_name: str = "windows"
    platform_app_version: str = "5.9.0"
    platform_app_id: int = 3704
    platform_app_source: str = "lv"
    platform_os: str = "windows"
    # 片段通用字段
    check_flag_video: int = 63487
    check_flag_text: int = 39
    check_flag_audio: int = 1
    material_category: str = "local"
    track_attribute: int = 0
    render_index_video: int = 0
    render_index_audio: int = 0
    render_index_text: int = 14000
    transition_duration_us: int = 500_000
    text_layer_weight: int = 1
    text_typesetting: int = 0
    text_alignment_default: int = 1
    extra: dict = field(default_factory=dict)


PROFILES: dict[str, SchemaProfile] = {
    "jianying_5_9": SchemaProfile(),
    "jianying_6_0": SchemaProfile(
        key="jianying_6_0",
        version=360000,
        new_version="120.0.0",
        app_version="6.0.0",
        platform_app_version="6.0.0",
    ),
}


def get_profile(key: str) -> SchemaProfile:
    if key not in PROFILES:
        raise KeyError(f"未知草稿 schema profile: {key}（可选：{sorted(PROFILES)}）")
    return PROFILES[key]


# ---------------------------------------------------------------------------
# 空容器（剪映要求这些字段存在，哪怕为空数组）
# ---------------------------------------------------------------------------

EMPTY_MATERIAL_LISTS = (
    "ai_translates", "audio_balance_effects", "audio_effects", "audio_fades", "audio_loop",
    "audio_pitch_shifts", "audio_track_indexes", "beats", "chromas", "color_curves",
    "common_keyframes", "digital_humans", "drafts", "effects", "filters", "green_screens",
    "handwrites", "hsl", "images", "log_color_wheels", "loudnesses", "manual_deformations",
    "masks", "material_colors", "multi_language_refs", "placeholders", "plugin_effects",
    "primary_color_wheels", "realtime_denoises", "shapes", "smart_crops", "smart_relights",
    "stickers", "tail_leaders", "text_templates", "video_effects", "video_trackings",
    "vocal_beautifys",
)

EMPTY_CONFIG_LISTS = (
    "adjusts", "audio_effects", "audio_fades", "audio_pitch_shifts", "audio_tones", "beats",
    "chromas", "color_curves", "digital_humans", "drafts", "effects", "filters",
    "green_screens", "handwrites", "hsl", "images", "log_color_wheels", "loudnesses",
    "manual_deformations", "masks", "material_colors", "multi_language_refs", "placeholders",
    "plugin_effects", "primary_color_wheels", "realtime_denoises", "shapes", "smart_crops",
    "smart_relights", "stickers", "tail_leaders", "text_templates", "video_effects",
    "video_trackings", "vocal_beautifys",
)


def build_materials_container() -> dict:
    container: dict = {name: [] for name in EMPTY_MATERIAL_LISTS}
    container.update({
        "audios": [],
        "canvases": [],
        "material_animations": [],
        "sound_channel_mappings": [],
        "speeds": [],
        "texts": [],
        "transitions": [],
        "videos": [],
        "vocal_separations": [],
    })
    return container


def build_config_container() -> dict:
    container: dict = {name: [] for name in EMPTY_CONFIG_LISTS}
    container.update({
        "canvases": [],
        "material_animations": [],
        "sound_channel_mappings": [],
        "speeds": [],
        "transitions": [],
        "vocal_separations": [],
    })
    return container


# ---------------------------------------------------------------------------
# 素材工厂
# ---------------------------------------------------------------------------


def make_canvas_material() -> dict:
    return {
        "album_image": "",
        "blur": 0.0,
        "color": "",
        "id": new_id(),
        "image": "",
        "image_id": "",
        "image_name": "",
        "source_platform": 0,
        "team_id": "",
        "type": "canvas",
    }


def make_speed_material(speed: float = 1.0) -> dict:
    return {
        "curve_speed": None,
        "id": new_id(),
        "mode": 0,
        "speed": speed,
        "type": "speed",
    }


def make_sound_channel_mapping() -> dict:
    return {
        "audio_channel_mapping": 0,
        "id": new_id(),
        "is_config_open": False,
        "type": "none",
    }


def make_vocal_separation() -> dict:
    return {"id": new_id(), "type": "vocal_separation"}


def make_sticker_animation(animations: list | None = None) -> dict:
    return {
        "animations": animations or [],
        "id": new_id(),
        "multi_language_current": "none",
        "type": "sticker_animation",
    }


# ---------------------------------------------------------------------------
# 文字关键帧动画（common_keyframes 实现，不依赖剪映云端动画资源）
# ---------------------------------------------------------------------------


def _keyframe(time_us: int, value: float) -> dict:
    control = {"alpha": 1.0, "id": "", "mode": 0, "time_offset": 0.0, "x": 0.0, "y": 0.0}
    return {
        "alpha": 1.0,
        "curveType": "Line",
        "id": new_id(),
        "kf_manual_type": 0,
        "left_control": dict(control),
        "material_id": "",
        "right_control": dict(control),
        "time": time_us,
        "value": value,
    }


def _keyframe_list(property_type: str, points: list[tuple[int, float]]) -> dict:
    return {
        "id": new_id(),
        "keyframe_list": [_keyframe(t, v) for t, v in points],
        "material_id": "",
        "property_type": property_type,
        "segment_id": "",
        "type": "sticker_animation",
    }


def make_text_intro_keyframes(kind: str, duration_sec: float) -> list[dict]:
    """文字入场动画关键帧。

    kind:
      "pop"   = 淡入 + 放大回弹（标题用）
      "fade"  = 淡入（逐镜头字幕用）
      "pulse" = 淡入 + 缓慢缩放呼吸（落版用）
    """
    duration_us = sec_to_us(max(0.2, duration_sec))
    fade_us = min(sec_to_us(0.3), duration_us)
    if kind == "pop":
        over_us = min(sec_to_us(0.35), duration_us)
        settle_us = min(sec_to_us(0.55), duration_us)
        return [
            _keyframe_list("KFTypeAlpha", [(0, 0.0), (fade_us, 1.0)]),
            _keyframe_list("KFTypeScaleX", [(0, 0.5), (over_us, 1.08), (settle_us, 1.0)]),
            _keyframe_list("KFTypeScaleY", [(0, 0.5), (over_us, 1.08), (settle_us, 1.0)]),
        ]
    if kind == "pulse":
        fade_us = min(sec_to_us(0.4), duration_us)
        return [
            _keyframe_list("KFTypeAlpha", [(0, 0.0), (fade_us, 1.0)]),
            _keyframe_list("KFTypeScaleX", [(0, 0.96), (duration_us // 2, 1.04), (duration_us, 1.0)]),
            _keyframe_list("KFTypeScaleY", [(0, 0.96), (duration_us // 2, 1.04), (duration_us, 1.0)]),
        ]
    return [_keyframe_list("KFTypeAlpha", [(0, 0.0), (fade_us, 1.0)])]


def make_zoom_keyframes(duration_sec: float, mode: str = "in") -> list[dict]:
    """镜头缓慢推近/拉远（Ken Burns），让静态画面也有运动感。

    mode: "in" = 1.0→1.06 推近；"out" = 1.06→1.0 拉远。
    """
    duration_us = sec_to_us(max(0.5, duration_sec))
    if mode == "out":
        points = [(0, 1.06), (duration_us, 1.0)]
    else:
        points = [(0, 1.0), (duration_us, 1.06)]
    return [
        _keyframe_list("KFTypeScaleX", points),
        _keyframe_list("KFTypeScaleY", points),
    ]


@dataclass
class MaterialRefs:
    """一个视频片段需要挂接的附属素材 id 集合。"""

    canvas_id: str
    speed_id: str
    sound_channel_mapping_id: str
    vocal_separation_id: str

    def to_list(self) -> list[str]:
        return [self.speed_id, self.canvas_id, self.sound_channel_mapping_id, self.vocal_separation_id]


def make_video_material(
    path: str,
    name: str,
    duration_sec: float,
    width: int,
    height: int,
    profile: SchemaProfile,
    has_audio: bool = False,
) -> dict:
    # 剪映 Windows 端路径使用正斜杠
    normalized = path.replace("\\", "/")
    return {
        "aigc_type": "none",
        "audio_fade": None,
        "cartoon_path": "",
        "category_id": "",
        "category_name": profile.material_category,
        "check_flag": profile.check_flag_video,
        "crop": {
            "lower_left_x": 0.0, "lower_left_y": 1.0,
            "lower_right_x": 1.0, "lower_right_y": 1.0,
            "upper_left_x": 0.0, "upper_left_y": 0.0,
            "upper_right_x": 1.0, "upper_right_y": 0.0,
        },
        "crop_ratio": "free",
        "crop_scale": 1.0,
        "duration": sec_to_us(duration_sec),
        "extra_type_option": 0,
        "formula_id": "",
        "freeze": None,
        "has_audio": has_audio,
        "height": height,
        "id": new_id(),
        "intensifies_audio_path": "",
        "intensifies_path": "",
        "is_ai_generate_content": False,
        "is_copyright": False,
        "is_text_edit_overdub": False,
        "is_unified_beauty_mode": False,
        "local_id": "",
        "local_material_id": "",
        "material_id": "",
        "material_name": name,
        "material_url": "",
        "matting": {
            "flag": 0, "has_use_quick_brush": False, "has_use_quick_eraser": False,
            "interactiveTime": [], "path": "", "strokes": [],
        },
        "media_path": "",
        "object_locked": None,
        "origin_material_id": "",
        "path": normalized,
        "picture_from": "none",
        "picture_set_category_id": "",
        "picture_set_category_name": "",
        "request_id": "",
        "reverse_intensifies_path": "",
        "reverse_path": "",
        "smart_motion": None,
        "source": 0,
        "source_platform": 0,
        "stable": None,
        "team_id": "",
        "type": "video",
        "video_algorithm": {
            "algorithms": [], "complement_frame_config": None, "deflicker": None,
            "gameplay_configs": [], "motion_blur_config": None, "noise_reduction": None,
            "path": "", "quality_enhance": None, "smart_complement_frame": None,
            "story_video_configs": [], "super_resolution": None, "time_range": None,
        },
        "width": width,
    }


def make_text_material(
    content: str,
    profile: SchemaProfile,
    font_size: int = 8,
    text_color: str = "#FFFFFF",
    border_color: str = "#000000",
    border_width: float = 0.08,
    alignment: int = 1,
    line_spacing: float = 0.02,
    background_alpha: float = 0.0,
    bold: bool = False,
    has_shadow: bool = False,
    line_max_width: float = 0.85,
) -> dict:
    return {
        "add_type": 0,
        "alignment": alignment,
        "background_alpha": background_alpha,
        "background_color": "#000000",
        "background_height": 0.14,
        "background_horizontal_offset": 0.0,
        "background_round_radius": 0.0,
        "background_style": 0,
        "background_vertical_offset": 0.0,
        "background_width": 0.14,
        "base_content": "",
        "bold_width": 0.0,
        "border_alpha": 1.0,
        "border_color": border_color,
        "border_width": border_width,
        "check_flag": profile.check_flag_text,
        "combo_info": {"text_templates": []},
        "content": json_text_content(
            content,
            text_color=text_color,
            font_size=font_size,
            alignment=alignment,
            bold=bold,
        ),
        "font_category_id": "",
        "font_category_name": "",
        "font_id": "",
        "font_name": "",
        "font_path": "",
        "font_resource_id": "",
        "font_size": font_size,
        "font_source_platform": 0,
        "font_team_id": "",
        "font_title": "none",
        "font_url": "",
        "fonts": [],
        "force_apply_line_max_width": True,
        "global_alpha": 1.0,
        "group_id": "",
        "has_shadow": has_shadow,
        "id": new_id(),
        "initial_scale": 1.0,
        "inner_padding": 0.0,
        "is_rich_text": False,
        "italic_degree": 0,
        "ktv_color": "",
        "language": "",
        "layer_weight": profile.text_layer_weight,
        "letter_spacing": 0.0,
        "line_feed": 1,
        "line_max_width": line_max_width,
        "line_spacing": line_spacing,
        "lyrics_template_info": None,
        "name": "",
        "original_size": [],
        "preset_id": "",
        "preset_name": "",
        "recognize_type": 0,
        "relevance_segment": [],
        "shadow_alpha": 0.9,
        "shadow_angle": -45.0,
        "shadow_color": "#000000",
        "shadow_distance": 5.0,
        "shadow_smoothing": 0.0,
        "stroke_color": border_color,
        "stroke_width": border_width,
        "style_name": "",
        "sub_type": 0,
        "text_alpha": 1.0,
        "text_color": text_color,
        "text_size": font_size,
        "type": "text",
        "typesetting": profile.text_typesetting,
        "underline": False,
        "vfx_segment_id": "",
    }


def hex_to_rgb_floats(hex_color: str) -> list[float]:
    """'#RRGGBB' -> [r, g, b]（0~1 浮点，剪映富文本颜色格式）。"""
    value = (hex_color or "").lstrip("#")
    if len(value) != 6:
        return [1.0, 1.0, 1.0]
    try:
        return [round(int(value[i:i + 2], 16) / 255.0, 4) for i in (0, 2, 4)]
    except ValueError:
        return [1.0, 1.0, 1.0]


def json_text_content(
    content: str,
    text_color: str = "#FFFFFF",
    font_size: int = 8,
    alignment: int = 1,
    bold: bool = False,
) -> str:
    """剪映文本内容使用带内联样式的 JSON 字符串。

    重要：剪映 11.x 渲染文本以这里的内容为最终依据——
    alignment 必须写进内联样式（1=居中），否则按左对齐渲染；
    颜色以内联 fill 为准（不写死白色）。
    """
    import json

    payload = {
        "text": content,
        "styles": [{
            "alignment": alignment,
            "bold": bold,
            "fill": {
                "alpha": 1.0,
                "content": {"solid": {"color": hex_to_rgb_floats(text_color)}},
                "type": "solid",
            },
            "font": {"id": "", "path": ""},
            "italic": False,
            "letter_spacing": 0.0,
            "line_feed": 1,
            "line_spacing": 0.01,
            "range": [0, len(content)],
            "size": float(font_size),
            "strokes": [],
            "underline": False,
            "underline_offset": 0.0,
            "underline_width": 0.0,
            "using_composite": False,
        }],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def make_audio_material(
    path: str,
    name: str,
    duration_sec: float,
    profile: SchemaProfile,
) -> dict:
    normalized = path.replace("\\", "/")
    return {
        "app_id": 0,
        "category_id": "",
        "category_name": "local",
        "check_flag": profile.check_flag_audio,
        "duration": sec_to_us(duration_sec),
        "effect_id": "",
        "formula_id": "",
        "id": new_id(),
        "intensifies_path": "",
        "local_material_id": "",
        "music_id": "",
        "name": name,
        "path": normalized,
        "request_id": "",
        "resource_id": "",
        "source_platform": 0,
        "team_id": "",
        "text_id": "",
        "tone_category_id": "",
        "tone_category_name": "",
        "tone_effect_id": "",
        "tone_effect_name": "",
        "tone_speaker": "",
        "tone_type": "",
        "type": "extract_music",
        "video_id": "",
        "wave_points": [],
    }


def make_transition_material(
    name: str = "叠化",
    profile: SchemaProfile | None = None,
    duration_sec: float | None = None,
) -> dict:
    profile = profile or SchemaProfile()
    duration_us = sec_to_us(duration_sec) if duration_sec is not None else profile.transition_duration_us
    return {
        "category_id": "",
        "category_name": "转场",
        "duration": duration_us,
        "effect_id": "",
        "id": new_id(),
        "is_overlap": True,
        "name": name,
        "platform": "all",
        "resource_id": "",
        "type": "transition",
    }


# ---------------------------------------------------------------------------
# 片段工厂
# ---------------------------------------------------------------------------


def make_video_segment(
    material_id: str,
    refs: MaterialRefs,
    source_start_sec: float,
    duration_sec: float,
    target_start_sec: float,
    volume: float,
    profile: SchemaProfile,
    render_index: int = 0,
    keyframes: list[dict] | None = None,
) -> dict:
    return {
        "cartoon": False,
        "clip": {
            "alpha": 1.0,
            "flip": {"horizontal": False, "vertical": False},
            "rotation": 0.0,
            "scale": {"x": 1.0, "y": 1.0},
            "transform": {"x": 0.0, "y": 0.0},
        },
        "common_keyframes": keyframes or [],
        "enable_adjust": True,
        "enable_color_correct_adjust": False,
        "enable_color_curves": True,
        "enable_color_match_adjust": False,
        "enable_color_wheels": True,
        "enable_lut": True,
        "enable_smart_color_adjust": False,
        "extra_material_refs": refs.to_list(),
        "group_id": "",
        "id": new_id(),
        "is_placeholder": False,
        "is_tone_modify": False,
        "keyframe_refs": [],
        "last_nonzero_volume": 1.0,
        "material_id": material_id,
        "render_index": render_index,
        "responsive_layout": {
            "enable": False, "horizontal_layout_type": "", "horizontal_responsive_type": "",
            "vertical_layout_type": "", "vertical_responsive_type": "",
        },
        "reverse": False,
        "source_timerange": {
            "duration": sec_to_us(duration_sec),
            "start": sec_to_us(source_start_sec),
        },
        "speed": 1.0,
        "target_timerange": {
            "duration": sec_to_us(duration_sec),
            "start": sec_to_us(target_start_sec),
        },
        "template_id": "",
        "template_scene": "default",
        "track_attribute": profile.track_attribute,
        "track_render_index": render_index,
        "uniform_scale": {"on": True, "value": 1.0},
        "visible": True,
        "volume": round(max(0.0, min(1.0, volume)), 3),
    }


def make_text_segment(
    material_id: str,
    animation_id: str,
    target_start_sec: float,
    duration_sec: float,
    position_x: float,
    position_y: float,
    profile: SchemaProfile,
    render_index: int = 14000,
    keyframes: list[dict] | None = None,
) -> dict:
    return {
        "clip": {
            "alpha": 1.0,
            "flip": {"horizontal": False, "vertical": False},
            "rotation": 0.0,
            "scale": {"x": 1.0, "y": 1.0},
            "transform": {"x": position_x, "y": position_y},
        },
        "common_keyframes": keyframes or [],
        "enable_adjust": False,
        "extra_material_refs": [animation_id],
        "group_id": "",
        "id": new_id(),
        "is_placeholder": False,
        "keyframe_refs": [],
        "material_id": material_id,
        "render_index": render_index,
        "source_timerange": {"duration": 0, "start": 0},
        "target_timerange": {
            "duration": sec_to_us(duration_sec),
            "start": sec_to_us(target_start_sec),
        },
        "template_id": "",
        "template_scene": "default",
        "track_attribute": profile.track_attribute,
        "track_render_index": 0,
        "visible": True,
        "volume": 1.0,
    }


def make_audio_segment(
    material_id: str,
    speed_id: str,
    sound_channel_mapping_id: str,
    source_start_sec: float,
    duration_sec: float,
    target_start_sec: float,
    volume: float,
    profile: SchemaProfile,
) -> dict:
    return {
        "clip": {
            "alpha": 1.0,
            "flip": {"horizontal": False, "vertical": False},
            "rotation": 0.0,
            "scale": {"x": 1.0, "y": 1.0},
            "transform": {"x": 0.0, "y": 0.0},
        },
        "common_keyframes": [],
        "enable_adjust": False,
        "extra_material_refs": [speed_id, sound_channel_mapping_id],
        "group_id": "",
        "id": new_id(),
        "is_placeholder": False,
        "keyframe_refs": [],
        "last_nonzero_volume": 1.0,
        "material_id": material_id,
        "render_index": 0,
        "reverse": False,
        "source_timerange": {
            "duration": sec_to_us(duration_sec),
            "start": sec_to_us(source_start_sec),
        },
        "speed": 1.0,
        "target_timerange": {
            "duration": sec_to_us(duration_sec),
            "start": sec_to_us(target_start_sec),
        },
        "template_id": "",
        "template_scene": "default",
        "track_attribute": profile.track_attribute,
        "track_render_index": 0,
        "visible": True,
        "volume": round(max(0.0, min(1.0, volume)), 3),
    }


def make_track(track_type: str, segments: list[dict], name: str = "") -> dict:
    return {
        "attribute": 0,
        "flag": 0,
        "id": new_id(),
        "is_default_name": name == "",
        "name": name,
        "segments": segments,
        "type": track_type,
    }


def now_us() -> int:
    return int(time.time() * 1_000_000)
