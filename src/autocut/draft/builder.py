"""剪映专业版草稿生成器。

输入：Timeline（纯领域模型）+ BrandKit（样式）+ 素材路径
输出：可直接在剪映专业版「草稿」列表中打开的工程目录

    <草稿根目录>/<草稿名>/
        draft_content.json
        draft_meta_info.json

设计约束：
- 生成器不访问数据库、不做 IO 抉择之外的业务判断，保证可单测、可回归；
- 所有版本相关字段来自 SchemaProfile，不散落在代码里；
- 生成后由 validator 做内部一致性校验（引用完整、时间轴无重叠、总时长一致）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..creative.brand import BrandKit, TextStyle
from ..errors import DraftBuildError
from ..logging_setup import get_logger
from ..models import Timeline
from . import schema as S

logger = get_logger("draft.builder")

DRAFT_CONTENT_FILE = "draft_content.json"
DRAFT_META_FILE = "draft_meta_info.json"


@dataclass
class DraftResult:
    draft_dir: Path
    content_path: Path
    meta_path: Path
    draft_name: str
    duration_sec: float
    video_count: int
    text_count: int
    audio_count: int
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "draft_dir": str(self.draft_dir),
            "draft_name": self.draft_name,
            "duration_sec": self.duration_sec,
            "video_segments": self.video_count,
            "text_segments": self.text_count,
            "audio_segments": self.audio_count,
            "warnings": self.warnings,
        }


def safe_folder_name(name: str) -> str:
    cleaned = name.strip().replace("/", "-").replace("\\", "-").replace(":", "-")
    cleaned = "".join(ch for ch in cleaned if ch not in '<>:"|?*')
    return cleaned[:60] or "autocut-draft"


class DraftBuilder:
    def __init__(
        self,
        output_root: Path,
        brand_kit: BrandKit | None = None,
        profile_key: str = "jianying_5_9",
        draft_name_suffix: str = "-AutoCut",
    ) -> None:
        self.output_root = Path(output_root)
        self.brand_kit = brand_kit or BrandKit()
        self.profile = S.get_profile(profile_key)
        self.draft_name_suffix = draft_name_suffix

    # ------------------------------------------------------------------
    def build(
        self,
        timeline: Timeline,
        overwrite: bool = True,
        material_durations: dict[str, float] | None = None,
    ) -> DraftResult:
        if not timeline.clips:
            raise DraftBuildError("时间线没有片段，拒绝生成空草稿")

        material_durations = material_durations or {}
        draft_name = safe_folder_name(f"{timeline.project_name}{self.draft_name_suffix}")
        draft_dir = self.output_root / draft_name
        if draft_dir.exists() and not overwrite:
            raise DraftBuildError(f"草稿目录已存在: {draft_dir}")
        draft_dir.mkdir(parents=True, exist_ok=True)

        warnings: list[str] = []
        materials = S.build_materials_container()
        config = S.build_config_container()

        video_track = self._build_video_track(timeline, materials, config, warnings, material_durations)
        text_tracks = self._build_text_tracks(timeline, materials, config)
        audio_track = self._build_audio_track(timeline, materials, config, warnings)

        tracks = [video_track] + text_tracks
        if audio_track:
            tracks.append(audio_track)

        content_end = self._content_end(video_track, text_tracks, audio_track)
        duration_us = S.sec_to_us(max(cursor_end(timeline), content_end))

        content = self._build_content(timeline, draft_name, materials, config, tracks, duration_us)
        meta = self._build_meta(timeline, draft_name, draft_dir, materials, duration_us)

        content_path = draft_dir / DRAFT_CONTENT_FILE
        meta_path = draft_dir / DRAFT_META_FILE
        content_path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        result = DraftResult(
            draft_dir=draft_dir,
            content_path=content_path,
            meta_path=meta_path,
            draft_name=draft_name,
            duration_sec=S.us_to_sec(duration_us),
            video_count=len(video_track["segments"]),
            text_count=sum(len(track["segments"]) for track in text_tracks),
            audio_count=len(audio_track["segments"]) if audio_track else 0,
            warnings=warnings,
        )
        logger.info("草稿已生成: %s（时长 %.1fs）", draft_dir, result.duration_sec)
        return result

    # ------------------------------------------------------------------
    def _build_video_track(
        self,
        timeline: Timeline,
        materials: dict,
        config: dict,
        warnings: list[str],
        material_durations: dict[str, float],
    ) -> dict:
        segments: list[dict] = []
        material_cache: dict[str, dict] = {}

        for clip in timeline.clips:
            path = Path(clip.material_path)
            if not path.exists():
                warnings.append(f"素材文件不存在，草稿中需手动重新链接：{clip.material_path}")

            key = clip.material_path
            if key not in material_cache:
                needed = clip.source_start_sec + clip.duration_sec
                known = material_durations.get(clip.material_path, 0.0)
                material = S.make_video_material(
                    path=str(path),
                    name=path.name,
                    duration_sec=max(needed, known, clip.duration_sec),
                    width=timeline.width,
                    height=timeline.height,
                    profile=self.profile,
                    has_audio=clip.volume > 0,
                )
                materials["videos"].append(material)
                material_cache[key] = material

            canvas = S.make_canvas_material()
            speed = S.make_speed_material(1.0)
            channel = S.make_sound_channel_mapping()
            vocal = S.make_vocal_separation()
            for item, bucket in (
                (canvas, "canvases"), (speed, "speeds"),
                (channel, "sound_channel_mappings"), (vocal, "vocal_separations"),
            ):
                materials[bucket].append(item)
                config[bucket].append(item)

            refs = S.MaterialRefs(
                canvas_id=canvas["id"],
                speed_id=speed["id"],
                sound_channel_mapping_id=channel["id"],
                vocal_separation_id=vocal["id"],
            )

            transition_id = ""
            if clip.transition:
                transition = S.make_transition_material(
                    clip.transition, self.profile, duration_sec=timeline.transition_duration_sec
                )
                materials["transitions"].append(transition)
                config["transitions"].append(transition)
                transition_id = transition["id"]

            # 镜头缓慢推近/拉远，交替方向，避免画面死板
            keyframes = []
            if timeline.ken_burns:
                keyframes = S.make_zoom_keyframes(
                    clip.duration_sec, mode="in" if clip.index % 2 == 0 else "out"
                )

            segment = S.make_video_segment(
                material_id=material_cache[key]["id"],
                refs=refs,
                source_start_sec=clip.source_start_sec,
                duration_sec=clip.duration_sec,
                target_start_sec=clip.target_start_sec,
                volume=clip.volume,
                profile=self.profile,
                render_index=clip.index,
                keyframes=keyframes,
            )
            if transition_id:
                segment["extra_material_refs"].append(transition_id)
            segments.append(segment)
        return S.make_track("video", segments, name="主视频")

    # ------------------------------------------------------------------
    def _build_text_tracks(self, timeline: Timeline, materials: dict, config: dict) -> list[dict]:
        subtitle_track = S.make_track("text", [], name="字幕")
        title_track = S.make_track("text", [], name="标题")

        def add_text(text: str, start: float, duration: float, style: TextStyle, track: dict,
                     animation_kind: str = "fade") -> None:
            if not text or duration <= 0:
                return
            material = S.make_text_material(
                content=text,
                profile=self.profile,
                font_size=style.font_size,
                text_color=style.text_color,
                border_color=style.border_color,
                border_width=style.border_width,
                alignment=style.alignment,
                line_spacing=style.line_spacing,
                background_alpha=style.background_alpha,
                bold=style.bold,
                has_shadow=style.has_shadow,
                line_max_width=style.line_max_width,
            )
            animation = S.make_sticker_animation()
            materials["texts"].append(material)
            materials["material_animations"].append(animation)
            config["material_animations"].append(animation)
            track["segments"].append(S.make_text_segment(
                material_id=material["id"],
                animation_id=animation["id"],
                target_start_sec=start,
                duration_sec=duration,
                position_x=style.position_x,
                position_y=style.position_y,
                profile=self.profile,
                keyframes=S.make_text_intro_keyframes(animation_kind, duration),
            ))

        # 片头标题：覆盖开场镜头（弹入动画）
        title_duration = min(timeline.title_duration_sec, max(0.0, timeline.total_duration_sec))
        add_text(timeline.title, 0.0, title_duration, self.brand_kit.title_style, title_track,
                 animation_kind="pop")

        # 逐镜头字幕（淡入）
        for clip in timeline.clips:
            add_text(
                clip.subtitle,
                clip.target_start_sec,
                clip.duration_sec,
                self.brand_kit.subtitle_style,
                subtitle_track,
            )

        # 品牌落版（淡入 + 缓慢缩放呼吸）
        if timeline.outro_text:
            outro_duration = min(timeline.outro_duration_sec, timeline.total_duration_sec)
            outro_start = max(0.0, timeline.total_duration_sec - outro_duration)
            add_text(timeline.outro_text, outro_start, outro_duration, self.brand_kit.outro_style,
                     title_track, animation_kind="pulse")

        tracks = [subtitle_track]
        if title_track["segments"]:
            tracks.append(title_track)
        return tracks

    # ------------------------------------------------------------------
    def _build_audio_track(
        self,
        timeline: Timeline,
        materials: dict,
        config: dict,
        warnings: list[str],
    ) -> dict | None:
        music = timeline.music
        if music is None:
            return None
        music_path = Path(music.path)
        if not music_path.exists():
            warnings.append(f"音乐文件不存在，草稿中需手动重新导入：{music.path}")
        duration = min(timeline.total_duration_sec, max(1.0, music.duration_sec or timeline.total_duration_sec))

        audio = S.make_audio_material(str(music_path), music.name or music_path.name, duration, self.profile)
        speed = S.make_speed_material(1.0)
        channel = S.make_sound_channel_mapping()
        materials["audios"].append(audio)
        for item, bucket in ((speed, "speeds"), (channel, "sound_channel_mappings")):
            materials[bucket].append(item)
            config[bucket].append(item)

        segment = S.make_audio_segment(
            material_id=audio["id"],
            speed_id=speed["id"],
            sound_channel_mapping_id=channel["id"],
            source_start_sec=timeline.music_start_sec,
            duration_sec=duration,
            target_start_sec=0.0,
            volume=timeline.music_volume,
            profile=self.profile,
        )
        return S.make_track("audio", [segment], name="背景音乐")

    # ------------------------------------------------------------------
    def _content_end(self, video_track: dict, text_tracks: list[dict], audio_track: dict | None) -> float:
        ends: list[float] = []
        for track in [video_track, *text_tracks, *([audio_track] if audio_track else [])]:
            for segment in track["segments"]:
                target = segment["target_timerange"]
                ends.append(S.us_to_sec(target["start"] + target["duration"]))
        return max(ends) if ends else 0.0

    # ------------------------------------------------------------------
    def _build_content(
        self,
        timeline: Timeline,
        draft_name: str,
        materials: dict,
        config: dict,
        tracks: list[dict],
        duration_us: int,
    ) -> dict:
        profile = self.profile
        return {
            "canvas_config": {
                "height": timeline.height,
                "ratio": "original",
                "width": timeline.width,
            },
            "color_space": 0,
            "config": config,
            "cover": None,
            "create_time": 0,
            "duration": duration_us,
            "extra_info": None,
            "fps": timeline.fps,
            "free_render_index_mode_on": False,
            "id": S.new_id(),
            "keyframe_graph_list": [],
            "keyframes": {
                "adjusts": [], "audios": [], "effects": [], "filters": [], "handwrites": [],
                "stickers": [], "texts": [], "videos": [],
            },
            "last_modified_platform": {
                "app_id": profile.app_id,
                "app_source": profile.app_source,
                "app_version": profile.app_version,
                "device_id": "",
                "hard_disk_id": "",
                "mac_address": "",
                "os": profile.os_name,
                "os_version": "",
            },
            "materials": materials,
            "mutable_config": None,
            "name": draft_name,
            "new_version": profile.new_version,
            "platform": {
                "app_id": profile.platform_app_id,
                "app_source": profile.platform_app_source,
                "app_version": profile.platform_app_version,
                "device_id": "",
                "hard_disk_id": "",
                "mac_address": "",
                "os": profile.platform_os,
                "os_version": "",
            },
            "relationships": [],
            "render_index_track_mode_on": True,
            "retouch_cover": None,
            "smart_cover": None,
            "source": "default",
            "static_cover_image_path": "",
            "time_marks": None,
            "tracks": tracks,
            "update_time": 0,
            "version": profile.version,
        }

    # ------------------------------------------------------------------
    def _build_meta(
        self,
        timeline: Timeline,
        draft_name: str,
        draft_dir: Path,
        materials: dict,
        duration_us: int,
    ) -> dict:
        draft_materials = []
        for video in materials["videos"]:
            draft_materials.append({
                "create_time": 0,
                "duration": video["duration"],
                "extra_info": "",
                "file_Path": video["path"],
                "height": video["height"],
                "id": video["id"],
                "import_time": 0,
                "import_time_ms": 0,
                "item_source": 1,
                "material_id": video["id"],
                "metetype": "video",
                "roughcut_time_range": {"duration": -1, "start": -1},
                "sub_time_range": {"duration": -1, "start": -1},
                "type": 0,
                "width": video["width"],
            })
        for audio in materials["audios"]:
            draft_materials.append({
                "create_time": 0,
                "duration": audio["duration"],
                "extra_info": audio["name"],
                "file_Path": audio["path"],
                "height": 0,
                "id": audio["id"],
                "import_time": 0,
                "import_time_ms": 0,
                "item_source": 1,
                "material_id": audio["id"],
                "metetype": "music",
                "roughcut_time_range": {"duration": -1, "start": -1},
                "sub_time_range": {"duration": -1, "start": -1},
                "type": 0,
                "width": 0,
            })

        return {
            "draft_cloud_capcut_purchase_info": "",
            "draft_cloud_last_action_download": False,
            "draft_cloud_purchase_info": "",
            "draft_cloud_template_id": "",
            "draft_cloud_tutorial_info": "",
            "draft_cloud_videocut_purchase_info": "",
            "draft_cover": "",
            "draft_fold_path": str(draft_dir).replace("\\", "/"),
            "draft_id": S.new_id(),
            "draft_is_ai_shorts": False,
            "draft_is_invisible": False,
            "draft_json_file": str(draft_dir / DRAFT_CONTENT_FILE).replace("\\", "/"),
            "draft_materials": draft_materials,
            "draft_materials_copied_info": [],
            "draft_name": draft_name,
            "draft_need_rename_folder": False,
            "draft_new_version": "",
            "draft_removable_storage_device": "",
            "draft_root_path": str(self.output_root).replace("\\", "/"),
            "draft_segment_extra_info": [],
            "draft_timeline_materials_size_": 0,
            "draft_type": "",
            "tm_draft_create": S.now_us(),
            "tm_draft_modified": S.now_us(),
            "tm_draft_removed": 0,
            "tm_duration": duration_us,
        }


def cursor_end(timeline: Timeline) -> float:
    if not timeline.clips:
        return 0.0
    last = timeline.clips[-1]
    return round(last.target_start_sec + last.duration_sec, 3)
