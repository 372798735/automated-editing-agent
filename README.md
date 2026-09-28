# AutoCut Agent —— 智能剪辑智能体（MVP）

> 面向地板铺装商家的短视频智能剪辑流水线：**剪映云空间素材 → 智能选片 → 文案生成 → 智能配乐 → 剪映专业版草稿**。
> 目标：把单条视频的人工剪辑成本从 30-60 分钟压到 5 分钟以内（人工只做精修，不做从零剪）。

**当前状态**：MVP 已可运行。零第三方依赖即可跑通全链路（素材接入 → 分析 → 文案 → 配乐 → 时间线 → 草稿 → 预览），
装了 FFmpeg / 配了 LLM Key / 装了 Playwright 后能力自动增强。

---

## 1. 快速开始

**不熟悉命令行：用图形操作台（推荐）**

```bash
python start.py --ui
```

浏览器自动打开，鼠标点选：选素材来源 → 读取项目 → 勾选 → 开始生成 → 页面里看分镜预览。全程不用记参数。

**命令行跑一遍（无需素材/依赖/剪映）**

```bash
python start.py
```

跑完自动打开预览页：

```bash
python start.py --open
```

> 如果 `python` 不在 PATH，用项目里的 `autocut.bat`（双击即可），
> 或直接给解释器绝对路径。详细说明见 [`QUICKSTART.md`](QUICKSTART.md)。

**其余常用命令**

```bash
# 启动操作台（也可用 python run.py serve --port 9000）
python run.py serve

# 环境体检：告诉你缺什么、缺了会影响什么（不会阻断运行）
python run.py doctor

# 用自己的真实素材（目录结构：<月份>/<项目>/<素材文件>）
python start.py --source local --path "D:/素材/地板" --open

# 从剪映云空间同步（需先扫码登录一次）
python run.py capcut-login      # 扫码登录，保存登录态
python run.py capcut-record     # 录制云空间页面结构与接口，用于选择器校准
python start.py --source capcut-web --month 2026-09
```

> `start.py` = 给人用（图形操作台 / 自动开预览 / 起本地服务）；
> `run.py` = 给脚本用（纯命令行，等价于 `python -m autocut`，但免设 `PYTHONPATH`）。
> 若已 `pip install -e .`，也可直接用 `autocut` 命令。

---

## 2. 命令一览

| 命令 | 作用 |
| --- | --- |
| `autocut serve` | **启动图形操作台**：浏览器里选素材、勾项目、生成草稿、看预览 |
| `autocut doctor` | 环境体检：Python / FFmpeg / LLM / Playwright / 剪映草稿目录，逐项说明影响 |
| `autocut list --source ...` | 只发现项目不执行（干跑），确认目录结构对不对 |
| `autocut run --source ...` | 跑单个项目，端到端产出草稿 + 预览 + 渲染脚本 |
| `autocut batch --source ...` | 批量跑一个月份下的全部项目，汇总报告 `workspace/batch_report.json` |
| `autocut capcut-login` | 剪映云空间扫码登录，登录态存 `workspace/.auth/capcut_state.json` |
| `autocut capcut-record` | 录制云空间页面 DOM / 截图 / 网络请求，产出选择器校准材料 |

常用参数：

```bash
--source mock|local|capcut-web   素材源
--path <目录>                     local 源的素材根目录
--project <名称或id>              只处理指定项目
--month 2026-09 --month 2026-08   只处理指定月份（可多次）
--template floor_customer         指定文案模板
--music bgm-warm-01               指定背景音乐
--llm / --no-llm                  是否用大模型润色文案（默认关）
--set video.target_max_sec=35     临时覆盖任意配置项
```

---

## 3. 一次 run 产出什么

所有产物落在 `workspace/projects/<project_id>/`：

| 产物 | 内容 | 谁看 |
| --- | --- | --- |
| `materials.json` | 素材清单（时长/分辨率/码率/哈希） | 排查 |
| `shots.json` | 候选镜头清单，含每个镜头的打分细节 | 排查/调参 |
| `copywriting.json` | 标题、逐镜头字幕、话题标签 | 运营 |
| `selection_report.json` | **为什么选它 / 为什么淘汰** | 运营/调参 |
| `timeline.json` | 完整时间线（可回放、可复现） | 开发/复算 |
| `draft_validation.json` | 草稿自检结果（引用完整性、时长一致性） | 开发 |
| `preview.html` | **分镜预览页**：镜头顺序、字幕、配乐、选片理由一页看完 | 运营 |
| `render/render.ps1` `.sh` | FFmpeg 渲染脚本（不打开剪映也能出片） | 进阶 |
| `report.json` | 本次运行总报告（阶段耗时、告警、错误） | 全部 |

草稿本体在剪映草稿目录（默认 `%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft`，
未装剪映时退化为 `workspace/draft_output`），目录结构：

```
<草稿名>-AutoCut/
    draft_content.json      视频轨 + 字幕轨 + 标题轨 + 音乐轨
    draft_meta_info.json    草稿元信息与素材清单
```

---

## 4. 项目结构

```
src/autocut/
├── capabilities.py      环境能力探测（doctor 的数据来源）
├── config.py            配置加载与校验（json/yaml，零依赖）
├── models.py            领域模型（Material / Shot / Copywriting / Timeline ...）
├── llm.py               OpenAI 兼容客户端（结构化 JSON 输出 + 容错解析）
├── sources/             素材接入：base 抽象 / local 本地 / mock 演示 / capcut_web 云空间
├── media/               媒体层：mp4box 纯 Python 解析 / probe 探测 / frames 抽帧 / quality 打分
├── analysis/            镜头理解：heuristic 启发式 / vlm 多模态
├── creative/            创意层：copywriter 文案 / music 配乐 / brand 品牌套件
├── timeline/            时间线编排器（选片决策大脑，纯逻辑可单测）
├── draft/               剪映草稿：schema 版本档案 / builder 生成 / validator 校验
├── render/              FFmpeg 渲染脚本导出
├── web/                 图形操作台：server.py 本地 API 服务 + static/index.html 页面
├── preview.py           HTML 分镜故事板
├── pipeline.py          端到端编排 + 产物落盘
└── cli.py               命令行入口

config/     default / copy_templates / brand_kit / music_library / capcut_selectors.example
scripts/    demo_run / make_placeholder_music / make_sample_materials
tests/      40 项单测（标准库 unittest）

start.py    一键启动（跑流水线 + 开预览 + 起本地服务）
run.py      免 PYTHONPATH 的 CLI 启动器
autocut.bat Windows 双击启动
QUICKSTART.md  上手指南（怎么启动、产物在哪、怎么进剪映）
```

---

## 5. 能力矩阵：没有依赖也能跑，有依赖更准

| 能力 | 缺失时的行为 | 怎么增强 |
| --- | --- | --- |
| FFmpeg | 镜头打分退化为「元数据启发式」（码率/分辨率/时长），无抽帧分析、无缩略图、无渲染脚本 | `winget install Gyan.FFmpeg` |
| FFprobe | 用内置纯 Python MP4 解析器读时长/分辨率/帧率（MP4/MOV 有效） | 随 FFmpeg 一起安装 |
| LLM API Key | 文案走模板填槽（可用），不做个性化润色 | 设 `AUTOCUT_LLM_API_KEY`，并把 `llm.enabled` 设为 true |
| 多模态 VLM | 镜头类型按「位置 + 时长 + 画质」规则推断 | 同上再开 `llm.vision_enabled`（视觉模型） |
| Playwright | 无法自动同步云空间，改用本地素材目录 | `pip install playwright && playwright install chromium` |
| 剪映草稿目录 | 草稿写入 `workspace/draft_output`，手动拷进剪映即可 | 在 `config/default.json` 填 `draft.draft_root` |
| PyYAML | 只能用 `.json` 配置与模板 | `pip install pyyaml` 后可用 `.yaml` |

---

## 6. 关键配置（`config/default.json`）

| 配置段 | 关键项 | 说明 |
| --- | --- | --- |
| `video` | `width/height/fps` | 画布尺寸，默认 1080×1920 竖屏 |
| | `target_min_sec / target_max_sec` | 成片目标时长区间（默认 25-45s） |
| | `clip_min_sec / clip_max_sec` | 单个镜头时长区间（默认 2-6s） |
| | `max_clips / min_clips` | 镜头数上下限 |
| `selection` | `min_quality_score` | 入选最低分，调低会选进更多镜头 |
| | `max_clips_per_material` | 同一素材最多用几次（防重复机位） |
| | `detail_insert_ratio` | 特写镜头穿插比例 |
| `analysis` | `provider` | `heuristic` / `vlm` / `auto` |
| | `frames_per_shot` | 每个镜头抽几帧做分析 |
| `creative` | `template_file` | 文案模板库路径（**你自己的模板放这里**） |
| | `music_library_file` | 音乐库路径（**把你自己的音乐登记进去**） |
| | `banned_words` | 绝对化用语黑名单，出口强制过滤 |
| `draft` | `draft_root` | 剪映草稿目录（留空则用工作区目录） |
| | `schema_profile` | 草稿 schema 版本档案：`jianying_5_9` / `jianying_6_0` |

---

## 7. 三件需要你配合的事

### 7.1 文案模板与音乐（你的风格资产）

- **文案模板**：编辑 `config/copy_templates.json`，每条模板支持变量 `{community}{customer}{craft}{month}{brand}{area}{advantage}`。
  已内置三套：工程展示 / 工艺细节 / 业主案例。项目名形如「阳光花园-张先生-全屋通铺」时会自动解析出小区、客户、工艺。
- **音乐**：把自己的音乐放进 `assets/music/`，在 `config/music_library.json` 登记路径、时长、BPM、情绪标签（`clean` / `warm`），
  系统按「成片时长 + 工艺类型」自动匹配。当前两个文件是脚本合成的占位音乐，**发布前请替换为有商用授权的音乐**。
- **品牌样式**：`config/brand_kit.json` 控制片头标题、字幕、落版的字号、描边、位置。

### 7.2 剪映云空间选择器校准（一次性）

剪映云空间没有公开 API，页面 DOM 也会随版本变化，所以这里不写死选择器：

```bash
python -m autocut capcut-login     # 1) 扫码登录（登录态会保存，后续复用）
python -m autocut capcut-record    # 2) 打开云空间，手动进入「我的-云空间」点开一个月份和一个项目
```

录制产物在 `workspace/debug/`：`capcut_page.html`（页面结构）、`capcut_page.png`（截图）、
`capcut_network.json`（网络请求，含列表/下载接口候选）。把规律整理进 `config/capcut_selectors.json`
（模板见 `capcut_selectors.example.json`），之后 `--source capcut-web` 就能自动同步。

> 合规边界：只操作用户自己账号的常规页面交互，不绕过登录、限流或任何访问控制。

### 7.3 草稿 schema 校准（换剪映版本时）

`draft/schema.py` 把版本相关字段集中在 `SchemaProfile` 里。若剪映大版本升级后草稿打不开：
先用剪映手动建一个最简单的草稿（一段视频 + 一条字幕 + 一段音乐），对比它的 `draft_content.json`
与生成结果的差异，把变化收敛到 profile 里，不要散落到 builder。生成后自检由 `draft/validator.py` 负责。

---

## 8. 测试

```bash
python tests/run_all.py            # 40 项单测（标准库，无需安装依赖）
# 或安装 dev 依赖后：
pytest
```

覆盖重点：MP4 解析、时间线编排规则（钩子开头/完工收尾/时长约束/去重/字幕对齐）、
草稿生成与校验器（含人为破坏引用和时长的反向用例）、文案变量抽取与禁用词过滤、端到端流水线。

---

## 9. MVP 与设计文档的对应关系

| PRD 功能 | MVP 状态 | 说明 |
| --- | --- | --- |
| F1 云空间授权与增量同步 | 🟡 半实现 | 登录态持久化 + 录制校准已就绪；选择器需校准后才能自动同步；本地目录模式已完全可用 |
| F2 素材理解（探测 + 抽帧 + 分类） | ✅ | FFmpeg 抽帧打分（亮度/清晰度/稳定度）+ 场景切分；无 FFmpeg 时降级为元数据启发式 |
| F3 文案生成（模板 + LLM） | ✅ | 模板填槽 + 禁用词过滤；配 Key 后可 LLM 润色 |
| F3 配乐匹配 | ✅ | 音乐库 + 时长/情绪匹配；BPM 节拍吸附留给 V1.5 |
| F4 智能选片与草稿生成 | ✅ | 选片编排 + 四轨草稿 + 生成后自检 |
| F4 字幕/片头/落版样式 | ✅ | Brand Kit 可配置 |
| F5 任务队列与定时调度 | ⬜ 未实现 | MVP 为命令行同步执行；队列化（Celery）见技术架构文档 |
| F6 管理台 | ⬜ 未实现 | MVP 用 `preview.html` 分镜预览替代，看板见 V1.0 |
| F7 通知 | ⬜ 未实现 | 见 V1.5 |

> ⚠️ 已知限制：**草稿 schema 未在真实剪映客户端上验证**（当前开发机未安装剪映）。
> 到你的剪辑机器上第一次打开草稿时，请按 7.3 的流程核对；若打不开，把生成的 `draft_content.json`
> 和剪映自建草稿的对应文件一起给我，我来收敛 schema 差异。

---

## 10. 设计文档

| 文档 | 说明 |
| --- | --- |
| [01-产品设计文档-PRD.md](./01-产品设计文档-PRD.md) | 背景、场景、业务流程、功能清单、版本路线图、成功指标 |
| [02-技术架构设计文档.md](./02-技术架构设计文档.md) | 分层架构、ADR、模块详设、数据模型、API、技术栈、部署、风险 |

## 一句话架构

```
Playwright 同步云空间素材（按月/项目）
   → FFmpeg + 多模态 VLM 镜头理解与打分
   → LLM + RAG 文案生成（行业模板约束）
   → 智能选片编排 Timeline
   → 剪映专业版草稿生成器（视频/字幕/音乐多轨就位）
   → 人工精修导出
```

## 技术栈速览（完整版见技术架构文档）

`Python 3.12` · `FastAPI` · `Celery + Redis` · `MySQL + pgvector` · `MinIO` · `Playwright` · `FFmpeg / OpenCV / librosa` · `LLM 结构化输出 + RAG` · `多模态 VLM` · `Vue3 + TS` · `Docker Compose → K8s`
