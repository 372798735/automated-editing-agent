# AutoCut Agent 技术架构与实现细节分析

> 本文档基于对项目全部源码的逐文件阅读，从架构设计、核心模块、关键技术细节三个维度进行系统性分析。

---

## 一、项目概览

**AutoCut Agent** 是一条面向地板铺装行业的短视频智能剪辑流水线。它从剪映云空间（或本地目录）拉取施工素材，自动完成选片、文案生成、配乐匹配、时间线编排，最终产出可直接在剪映专业版中打开精修的草稿工程文件，将单条视频的人工剪辑成本从 30-60 分钟压缩到 5 分钟以内。

- **语言/运行时**: Python 3.10+（开发环境 3.12）
- **包管理**: setuptools + pyproject.toml，可选依赖（playwright / pyyaml / pytest）
- **核心设计哲学**: 零必装依赖 —— 核心流水线仅依赖 Python 标准库即可跑通，外部能力（FFmpeg / LLM / VLM / Playwright）存在则自动增强，缺失则优雅降级

---

## 二、总体架构

### 2.1 分层流水线架构

系统采用 **7 阶段串行流水线**，每个阶段产出可序列化的中间产物并落盘，保证可回放、可复现、可单测：

```
discover → fetch(素材接入)
  → analyze(切分 + 打分 + 分类)
  → copywrite(模板 + 可选 LLM)
  → music(配乐匹配)
  → timeline(选片编排)
  → draft(剪映草稿) + validate
  → preview(HTML 故事板) + render script
```

产物统一落在 `workspace/projects/<project_id>/`：

| 产物文件 | 内容 |
|---|---|
| `materials.json` | 素材清单（路径、时长、分辨率、探测来源） |
| `shots.json` | 镜头清单（时间区间、类型、分数、打分依据） |
| `copywriting.json` | 文案（标题、逐镜头字幕、话题标签） |
| `timeline.json` | 时间线（片段序列、字幕分配、配乐信息） |
| `selection_report.json` | 选片明细（为什么选它 / 为什么淘汰） |
| `draft/` | 剪映草稿目录（draft_content.json + draft_meta_info.json） |
| `preview.html` | 分镜预览页 |
| `render/` | FFmpeg 渲染脚本（PowerShell + Bash 双版本） |
| `report.json` | 本次运行总报告 |

### 2.2 架构层次关系

```
┌─────────────────────────────────────────────────┐
│  CLI (cli.py) / Web 操作台 (web/server.py)       │  接入层
├─────────────────────────────────────────────────┤
│  Pipeline (pipeline.py)                          │  编排层
├──────────┬──────────┬──────────┬─────────────────┤
│ Sources  │ Analysis │ Creative │ Timeline        │  能力层
│ (素材接入)│ (镜头分析)│ (文案配乐)│ (选片编排)     │
├──────────┴──────────┴──────────┼────────────────┤
│  Draft Builder  │  Validator  │  Preview/Render  │  产出层
│  (草稿生成)      │  (校验)     │  (预览/脚本)     │
├─────────────────┴─────────────┴─────────────────┤
│  Config / Errors / Logging / Capabilities        │  基础设施
└─────────────────────────────────────────────────┘
```

关键设计约束：**领域模型（models.py）是所有层的公共契约，Timeline 是草稿生成器唯一的输入，草稿层不反向依赖任何上层**。这种单向数据流保证了每层可独立单测。

---

## 三、核心模块技术细节

### 3.1 领域模型层 (`models.py`)

全部使用 `@dataclass` 表达，提供 `SerializableMixin`（`to_dict` / `to_json` / `from_dict`），保持「可序列化、可单测、无框架耦合」。

核心实体：

| 模型 | 职责 | 关键设计 |
|---|---|---|
| `Material` | 素材文件 | 记录 `probe_source`（ffprobe/mp4box/mock/fallback），便于排查探测链路 |
| `Shot` | 候选镜头 | `final_score` 属性 = 质量 70% + 观感 30%，带 `score_detail` 保证可解释 |
| `Timeline` | 成片时间线 | 纯逻辑层，可脱离剪映独立验证；`from_dict` 手动处理嵌套对象反序列化 |
| `Copywriting` | 文案产物 | 记录 `generator`（template / template+llm），便于追踪文案来源 |
| `MusicAsset` | 背景音乐 | 带 `loudness_ok` 标记，为后续音量归一化预留 |

**镜头类型枚举** (`ShotType`) 与 PRD 选片策略一一对应：`HOOK`（开场钩子）、`PROCESS`（过程展示）、`DETAIL`（细节特写）、`FINISH`（收尾）、`INVALID`（无效）。

### 3.2 素材接入层 (`sources/`)

统一契约（`MaterialSource` ABC）：
- `discover() → list[Project]`：发现项目（月份文件夹 → 项目文件夹）
- `fetch(project) → list[Material]`：拉取素材并完成探测

三种实现，由 `build_source()` 工厂统一构造：

| 实现 | 场景 | 技术要点 |
|---|---|---|
| `MockSource` | 演示/自测 | 合成确定性素材元数据（基于文件名哈希），不需要真实视频文件 |
| `LocalSource` | MVP 推荐 | 扫描本地/网络盘目录，两级结构（月份/项目），零依赖 |
| `CapCutWebSource` | 剪映云空间 | Playwright 浏览器自动化，分「校准层」和「执行层」两步 |

**剪映云空间接入的诚实设计**（`capcut_web.py`）：

由于剪映云空间没有公开 API 且页面 DOM 随版本变化，该模块分两层：

1. **校准层**（`capcut-record` 命令）：打开已登录浏览器，录制网络请求 + 页面快照 + 截图，落到 `workspace/debug/`，需要用户在自己的机器上配合完成
2. **执行层**（`CapCutWebSource`）：读取校准产物 `config/capcut_selectors.json` 中的选择器与接口规则完成枚举和下载

登录态管理：首次扫码授权后持久化 `storage_state`（Playwright 的 cookie/localStorage 快照），后续复用；通过检测特定 cookie 名（`sessionid`、`sid_tt` 等）判断登录成功。合规边界：仅操作用户自己账号的常规 UI 交互，不绕过登录/限流/访问控制。

### 3.3 媒体处理层 (`media/`)

**素材探测三级降级**（`probe.py`）：

```
ffprobe (最佳) → 内置 MP4 解析器 (mp4box.py) → 码率粗估 (fallback)
```

- `ffprobe`：通过 subprocess 调用，解析 JSON 输出提取时长/分辨率/帧率/音频流
- `mp4box.py`：纯 Python MP4/MOV 容器解析器，解析 moov/trak/mdhd 等盒子，不依赖任何第三方库
- `fallback-estimate`：按 128kbps 码率粗估时长，保证流水线不中断

**采样哈希去重**（`file_checksum`）：首尾各 1MB + 文件大小做 SHA-256，避免大视频全量读取。

**帧级画面分析**（`frames.py`）—— 关键技术亮点：

**不依赖 numpy / PIL / OpenCV**，通过 FFmpeg 的 `-vf scale=W:H,format=gray -f rawvideo -` 把帧降采样成灰度原始字节，在纯 Python 里统计：

- `brightness`：灰度均值（0-255）
- `contrast`：灰度标准差
- `sharpness`：水平相邻像素梯度均值（清晰度代理）
- `black_ratio`：极暗像素（<16）占比
- `motion`：帧间平均绝对差（稳定度代理）

场景切换检测：通过 `select='gt(scene,TH)' + showinfo` 解析 FFmpeg stderr 中的 `pts_time` 时间戳。

JPEG 帧抽取（供 VLM）：`-c:v mjpeg -q:v 4 -f image2 -`，输出原始 JPEG 字节，避免 Pillow 依赖。

**画质打分模型**（`quality.py`）：

两套评分函数，口径统一为 0-100：

1. `score_window`（有帧统计时）：
   - 画质分 = 亮度 35% + 清晰度 35% + 稳定度 20% + 对比度 10%
   - 黑屏惩罚：black_ratio > 0.5 时 ×0.3，> 0.2 时 ×0.7
   - 观感分 = 亮度 40% + 清晰度 40% + 对比度 20%

2. `score_from_metadata`（无 FFmpeg 时）：
   - 基于像素码率（bit/pixel/s）、分辨率、位置比例做启发式打分
   - 用 SHA-256 派生确定性扰动，保证同一素材每次运行结果一致（可复现）

### 3.4 镜头分析层 (`analysis/`)

**双通道设计**：启发式（默认） + VLM（可选增强），VLM 继承启发式，先跑兜底分数再用 VLM 覆盖语义判断。

**启发式分析器**（`heuristic.py`）—— 分两步：

**切分**（`segment`）：
- 有 FFmpeg 时用场景切换点 + 时长约束切分
- 无 FFmpeg 时按固定窗口切分（步长 = max(min_len, 5.0)）
- 过长片段按 max_len 均分

**打分与分类**（`analyze` + `_classify`）：
- 有帧统计时：按位置 + 清晰度判定（开头 HOOK_ZONE=18%、结尾 FINISH_ZONE=78%）
- 无帧统计时：按「片段在素材中的位置 + 时长」判定 + 确定性伪随机数辅助
- 无效镜头判定规则：时长过短、画质过低、亮度过暗、黑屏占比过高、运动幅度过大

**位置启发式依据**：素材开头常是场景交代（大场景钩子），结尾常是完工展示，短片段多为细节特写。

**VLM 分析器**（`vlm.py`）：
- 对每个候选镜头抽取 3 帧 JPEG（512px 宽），以 base64 内联发给视觉大模型
- System Prompt 要求返回结构化 JSON：镜头类型、画质分、观感分、一句话描述
- 任何一步失败（未配 Key / 网络异常 / JSON 解析失败）都回退到启发式结果
- 继承 `HeuristicAnalyzer`，保证切分逻辑复用

### 3.5 创意生成层 (`creative/`)

**文案引擎**（`copywriter.py`）—— 四步流程：

1. **变量抽取**：从项目名（如「阳光花园-张先生-全屋通铺」）解析出小区、客户称呼、工艺等变量；解析不出来的一律给中性默认值（不编造事实）
2. **模板填槽**：从模板库选模板，用变量渲染标题/字幕/标签；支持 JSON 和 YAML 两种模板格式
3. **LLM 润色**（可选）：把模板产物交给大模型改写，要求保持结构与长度约束，输出 JSON
4. **合规过滤**：绝对化用语（最好/第一/100%/永久等）强制替换或剔除，输出前必过

**字幕分配策略**（在 `TimelineOrchestrator._assign_subtitles` 中实现）：
- 首条文案锚定到 HOOK 镜头，末条文案锚定到 FINISH 镜头
- 中间文案顺次填充，避免「完工镜头配细节处理文案」的错位

**配乐引擎**（`music.py`）：
- 音乐资产库记录用户自有音乐的路径、时长、BPM、情绪标签
- 选择策略：过滤掉时长短于成片时长的音乐 → 按「情绪匹配 50% + 时长接近度 50%」排序
- 工艺到情绪的映射：通铺→clean、人字拼/鱼骨拼/实木→warm
- 无匹配时返回 None，草稿只出视频+字幕（不假装有音乐）

**品牌套件**（`brand.py`）：
- 把「样式」（TextStyle：字号/颜色/描边/位置）从「内容」里拆出来
- 片头标题、逐镜头字幕、落版各有独立样式，同一套视觉调性能被所有项目复用

### 3.6 时间线编排器 (`timeline/orchestrator.py`)

这是整个系统的**「决策大脑」**，也是唯一可以完全脱离 AI/剪映单测的纯逻辑模块。

**编排规则**（对应 PRD 3.2）：

1. 开场：HOOK 类最高分镜头，画面稳定（不超过 opening_sec）
2. 主体：PROCESS 按素材拍摄顺序排列（文件名数字前缀排序），DETAIL 按比例穿插（detail_insert_ratio=35%）
3. 收尾：FINISH 类镜头 + 品牌落版
4. 总时长裁剪到 [25s, 45s]，优先保留高分镜头
5. 同一素材默认最多使用 2 个镜头，避免重复机位
6. 镜头不足时按「逐级放宽阈值补足」（最多 3 级，每级降低 20% 最低分要求）

**关键技术细节**：

- `_trim_window`：从镜头窗口里居中裁出目标时长，尽量保留动作完整性
- `_fit_duration` 的 `try_add` 闭包：同时检查去重、时长约束、片段数上限、总时长上限
- `_reorder`：入选后重排为「开场 → 主体 → 收尾」叙事顺序
- 原声音量策略：配了音乐时 PROCESS 镜头保留 15% 原声（保留施工现场感），其他镜头静音由 BGM 主导

所有决策都写进 `selection_report`，包含 `selected`（选中明细）和 `dropped`（淘汰明细，最多 80 条），便于复核。

### 3.7 剪映草稿生成器 (`draft/`)

**草稿工程结构**：
```
<草稿根目录>/<项目名>-AutoCut/
  draft_content.json     时间线主体（素材、轨道、片段）
  draft_meta_info.json   元信息（草稿名、路径、素材清单）
```

**Schema 版本适配**（`schema.py`）—— 关键工程决策：

- 剪映升级会改动 schema 细节，因此把「版本相关」字段集中成 `SchemaProfile` dataclass
- 生成器只依赖 profile，不硬编码版本号
- 当前 profile 对应剪映专业版 5.9 系（version=360000），同时预置了 6.0 的 profile
- `EMPTY_MATERIAL_LISTS` / `EMPTY_CONFIG_LISTS`：剪映要求这些字段存在哪怕为空数组（约 35 个空桶），遗漏任何一个都可能导致草稿打开异常

**时间单位**：剪映内部使用微秒（`TIME_SCALE = 1_000_000`），`sec_to_us` / `us_to_sec` 做转换。

**ID 生成**：剪映内部 id 形态为 32 位大写十六进制（`uuid.uuid4().hex.upper()`）。

**轨道构建**（`builder.py`）：
- 视频轨：每个片段挂接 canvas（画布）、speed（变速）、sound_channel_mapping（声道）、vocal_separation（人声分离）四个附属素材
- 文本轨：分字幕轨和标题轨，文本内容使用带内联样式的 JSON 字符串（`json_text_content`）
- 音频轨：BGM + 音量曲线
- 转场：`video.transition_preset` 控制模式——`mixed` 从 `video.transitions` 池轮换（叠化/闪白/推近/拉远/擦除/雾化…），自动跳过与上一个相同的转场；`single` 只用 `video.transition`；`off` 全硬切。时长取 `video.transition_duration_sec`（默认 0.5s）
- 画面动感：每个片段自动加 Ken Burns 关键帧（奇偶交替 1.0→1.06 推近 / 1.06→1.0 拉远），由 `video.ken_burns` 开关
- 文字动画：标题=淡入+放大回弹，字幕=淡入，落版=淡入+呼吸缩放（均以 common_keyframes 写入，不依赖剪映云端资源）

**草稿一致性校验器**（`validator.py`）—— 6 项校验：

1. ID 唯一性（material / segment / track 全局不重复）
2. 引用完整性（segment.material_id、extra_material_refs 必须能找到）
3. 时间轴合法性（同轨道片段不重叠，0.1ms 容差）
4. 时长一致性（draft.duration ≥ 所有片段结束时间）
5. 路径存在性（缺失只告警，因为用户可能用网盘/移动硬盘）
6. 画布与片段分辨率

校验结果分 error / warning 两级：error 会让流水线中断并报错，warning 只提示风险。

### 3.8 渲染脚本导出 (`render/ffmpeg_script.py`)

V2 兜底通道的 MVP 形态：把时间线翻译成可审阅的 FFmpeg 命令。

**刻意只生成脚本而不自动执行**：渲染是重资源操作，交给人决定何时跑。

输出三份文件：
- `render.ps1`（PowerShell 版）
- `render.sh`（Bash 版）
- `render_plan.json`（结构化计划，可程序化消费）

渲染流程 5 步：逐片段裁剪归一（scale+crop 到竖屏）→ concat 拼接 → drawtext 字幕烧录 → 文件名对齐 → 混入 BGM（含 afade 淡出）。

字体查找：自动探测 Windows 系统字体（msyh.ttc → simhei.ttf → simsun.ttc），找不到则跳过字幕烧录步骤。

### 3.9 HTML 故事板预览 (`preview.py`)

把时间线渲染成一页可读的「分镜表」，运营在打开剪映之前就能判断片子对不对。

- 有 FFmpeg 时自动抽缩略图（320px 宽），没有时用占位块
- 包含：项目概要指标、时长比例条、片头文案+话题标签、配乐信息、逐镜头卡片（缩略图+类型标签+字幕+元数据）、选片明细表、注意事项
- 纯字符串拼接生成 HTML，无模板引擎依赖

### 3.10 Web 图形操作台 (`web/server.py`)

**纯标准库 HTTP 服务**，不依赖 Flask/FastAPI 等第三方框架：

- 使用 `http.server.ThreadingHTTPServer`，只监听 `127.0.0.1`，不对外网暴露
- 长任务（跑流水线）放在后台线程执行，前端轮询进度，避免请求超时
- `TaskState` dataclass 维护任务全生命周期状态（pending/running/done/failed/cancelled）
- 文件访问限制在「工作区」或「草稿根目录」内（`safe_path` 方法防越权读取）
- Windows 系统目录选择框通过 PowerShell `FolderBrowserDialog` 实现

接口一览：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/status` | 环境能力 + 工作区状态 |
| GET | `/api/projects` | 发现项目（按来源类型） |
| POST | `/api/pick-dir` | 弹出系统目录选择框 |
| POST | `/api/run` | 启动批量剪辑任务，返回 task_id |
| GET | `/api/task/<id>` | 查询任务进度 |
| POST | `/api/cancel/<id>` | 请求取消任务 |
| GET | `/api/file?path=...` | 读取工作区内产物文件 |
| POST | `/api/open` | 用系统程序打开产物路径 |
| GET | `/api/drafts` | 列出已生成草稿 |

### 3.11 配置系统 (`config.py`)

**零第三方依赖策略**：用 dataclass + 显式校验替代 Pydantic。

配置优先级：CLI 参数（`--set key=value`）> 环境变量 > `config/default.json` > 内置默认值。

- `deep_merge`：递归合并字典，override 优先
- `load_structured_file`：统一读取 JSON/YAML，YAML 需要 PyYAML（缺失时给出明确提示）
- `from_dict`：显式校验未知字段，防止配置拼写错误静默通过
- `resolved_draft_root`：草稿输出目录的三级降级策略（显式配置 > 剪映默认草稿目录 > 工作区目录）

配置段：
- `VideoConfig`：画布 1080×1920 竖屏、目标时长 25-45s、片段 2-6s、镜头数 5-12
- `SelectionConfig`：最低画质分 35、每素材最多 2 镜头、特写穿插比例 35%
- `AnalysisConfig`：默认启发式、每镜头抽 3 帧
- `LLMConfig`：默认 DeepSeek、视觉模型 qwen-vl-max
- `CreativeConfig`：禁用词表、最多 5 个话题标签
- `DraftConfig`：schema profile、预览页/渲染脚本开关

### 3.12 能力探测与降级 (`capabilities.py`)

`autocut doctor` 命令把运行环境能力打印成体检表：

| 能力 | 检测方式 | 缺失影响 |
|---|---|---|
| Python 3.10+ | `sys.version_info` | 硬性要求 |
| FFmpeg | `shutil.which` | 无法抽帧做画面分析，退回元数据启发式 |
| FFprobe | `shutil.which` | 使用内置 MP4 解析器 |
| LLM 文案润色 | 环境变量 API Key | 使用模板填槽（可用但少个性化） |
| 多模态 VLM | API Key + 标准库 | 使用启发式镜头分类 |
| Playwright | `importlib.find_spec` | 无法同步云空间，改用本地素材模式 |
| 剪映草稿目录 | 目录存在性 | 草稿写入 workspace/draft_output |
| PyYAML | `importlib.find_spec` | 使用 JSON 配置 |

### 3.13 LLM 客户端 (`llm.py`)

**极简 OpenAI 兼容客户端**，仅使用标准库 `urllib`：

- 不用 openai SDK：MVP 要求零必装依赖
- 接口完全兼容 `POST {base_url}/chat/completions`，DeepSeek / 通义 / 智谱 / 私有化 vLLM 都可直接接
- `extract_json`：容错解析模型输出中的 JSON（处理 ```json 代码块包裹和前后废话）
- 指数退避重试（max_retries=2，间隔 2^attempt 秒，上限 8 秒）
- `build_client`：安全构造，未配置 Key 时返回 None 而不是抛异常

### 3.14 异常体系 (`errors.py`)

统一异常基类 `AutoCutError`，子类按业务域划分：`ConfigError`、`SourceError`、`CapabilityError`、`MediaError`、`AnalysisError`、`CreativeError`、`DraftBuildError`、`DraftValidationError`。

CLI 层捕获 `AutoCutError` 后输出友好提示并以非 0 退出码结束，避免把 Python 栈回溯直接抛给运营人员。

### 3.15 告警折叠机制 (`pipeline.py`）

`collapse_warnings` 函数把重复度高的告警折叠成「计数 + 原因 + 怎么办」：

- 同类告警达到 3 条时触发折叠
- 折叠后只保留一条示例路径，既避免刷屏又不至于让用户无从排查
- 预置两组折叠规则：素材文件不存在（草稿需手动重链）、素材不存在（渲染会失败）

---

## 四、端到端流水线 (`pipeline.py`)

`Pipeline.run_project()` 是整个系统的主编排方法，7 个阶段顺序执行：

```
1. ingest    → source.fetch(project)           素材接入
2. analyze   → analyzer.analyze_all(materials)   镜头切分+打分+分类
3. copywrite → copywriter.generate(...)          文案生成
4. music     → selector.select(...)              配乐匹配
5. timeline  → orchestrator.build(...)            选片编排
6. draft     → builder.build(...) + validate()   草稿生成+校验
7. export    → render_preview + write_scripts    预览+渲染脚本
```

**错误处理策略**：
- `AutoCutError`：可预期业务错误，标记 FAILED，记录错误信息
- `Exception`：未预期错误，标记 FAILED，记录完整异常信息
- 每个阶段都有 `StageRecord`（名称/状态/耗时/明细），最终写入 `report.json`
- 全部阶段成功后标记 `DRAFT_READY`

**批量执行**（`run_batch`）：逐项目执行，汇总成功/失败数，落盘 `batch_report.json`。

---

## 五、技术栈与工程化

### 5.1 依赖策略

| 依赖 | 类型 | 用途 |
|---|---|---|
| Python 3.10+ | 硬性 | 运行时 |
| FFmpeg / FFprobe | 可选 | 媒体探测、抽帧分析、渲染脚本 |
| Playwright | 可选 | 剪映云空间浏览器自动化 |
| PyYAML | 可选 | YAML 配置与模板支持 |
| pytest / ruff | 开发 | 测试与代码规范 |
| openai SDK | **不使用** | 自研 urllib 客户端替代 |
| numpy / PIL / OpenCV | **不使用** | 纯 Python 灰度统计替代 |
| Pydantic | **不使用** | dataclass + 显式校验替代 |
| Flask / FastAPI | **不使用** | http.server 替代（Web 操作台） |

### 5.2 测试

- 测试本身使用标准库 `unittest`，pytest 仅提供更好的输出
- 测试文件：`test_mp4box.py`（MP4 解析）、`test_draft_builder.py`（草稿生成）、`test_pipeline.py`（端到端流水线）、`test_timeline.py`（时间线编排）、`test_copywriter.py`（文案生成）
- MockSource 保证无需真实视频文件即可跑通全链路

### 5.3 代码规范

- Ruff：line-length=120，target-version=py310，启用 E/F/W/I/UP/B 规则
- 全部使用 `from __future__ import annotations`，支持 3.10 语法
- 类型标注完备，函数签名使用 `str | None` 等 PEP 604 语法

### 5.4 CLI 命令

```
autocut doctor          环境体检
autocut list            列出发现的项目（干跑）
autocut run             跑单个项目
autocut batch           批量处理
autocut capcut-login    剪映云空间扫码登录
autocut capcut-record   录制云空间页面结构
autocut serve           启动图形操作台
```

支持 `--set key=value` 临时覆盖配置（如 `--set video.target_max_sec=40`），`_assign_override` 支持点分路径。

---

## 六、关键设计决策总结

| 决策 | 选择 | 理由 |
|---|---|---|
| 草稿生成 vs 直接渲染 | 自研 Draft Builder | 可控、可定制字幕/多轨样式，保留人工精修能力 |
| 镜头理解 | 启发式 + VLM 双通道 | VLM 能理解语义分类但不可靠，启发式兜底保证流水线永不中断 |
| 素材接入 | Playwright 浏览器自动化 | 剪映云空间无公开 API，网页端自动化最稳 |
| 帧分析 | 纯 Python 灰度统计 | 不引入 numpy/PIL/OpenCV，保持零依赖 |
| 配置校验 | dataclass + 显式校验 | 不引入 Pydantic，保持零依赖 |
| Web 服务 | http.server 标准库 | 不为本地小工具引入 Web 框架 |
| LLM 客户端 | urllib 标准库 | 不引入 openai SDK，接口兼容即可 |
| 时间线 | 纯逻辑层可单测 | 脱离 AI/剪映独立验证，是系统决策大脑 |
| 草稿 schema | SchemaProfile 版本适配 | 剪映版本升级会变更 schema，集中管理版本相关字段 |
| 校验 | 生成后立即自检 | 剪映对损坏草稿表现为「打开空白/崩溃」，在生成阶段暴露问题 |

---

## 七、设计文档 vs 实现差异

项目包含两份设计文档（PRD + 技术架构设计），描述的是**目标架构（V1.0+）**，当前代码库是 **MVP 实现**，存在以下差异：

| 设计文档描述 | MVP 实现 |
|---|---|
| Celery + Redis 任务队列 | 同步串行执行（pipeline.py），无异步队列 |
| MySQL + pgvector + MinIO | JSON 文件落盘（workspace/projects/），无数据库 |
| FastAPI + Pydantic v2 | http.server 标准库 + dataclass |
| Vue3 + TypeScript 前端 | 单页 HTML（web/static/index.html） |
| SQLAlchemy 2.0 async | 无 ORM，纯文件 IO |
| Whisper ASR | 未实现 |
| RAG 文案检索 | 模板填槽 + 可选 LLM，无向量检索 |
| librosa BPM 提取 | 音乐库 JSON 配置，无音频分析 |

这些差异符合 PRD 的版本规划：MVP 阶段聚焦「单项目端到端产出可用草稿」，数据库/队列/前端等基础设施在 V1.0 阶段引入。

---

## 八、项目目录结构

```
automatedEditingAgent/
├── 01-产品设计文档-PRD.md          产品需求文档
├── 02-技术架构设计文档.md           目标技术架构
├── pyproject.toml                 包定义（零必装依赖）
├── run.py                         启动器（免配 PYTHONPATH）
├── config/
│   ├── default.json               默认配置
│   ├── brand_kit.json             品牌套件（标题/字幕/落版样式）
│   ├── copy_templates.json        文案模板库
│   ├── music_library.json         音乐资产库
│   └── capcut_selectors.example.json  云空间选择器模板
├── src/autocut/
│   ├── __init__.py
│   ├── __main__.py                python -m autocut 入口
│   ├── cli.py                     CLI 命令（doctor/run/batch/list/serve）
│   ├── pipeline.py                端到端流水线编排
│   ├── config.py                  配置加载与校验
│   ├── models.py                  领域模型（dataclass）
│   ├── errors.py                  异常体系
│   ├── capabilities.py            运行环境能力探测
│   ├── llm.py                     OpenAI 兼容 LLM 客户端
│   ├── logging_setup.py           日志与 trace_id
│   ├── preview.py                 HTML 故事板预览
│   ├── sources/                   素材接入层
│   │   ├── base.py                MaterialSource ABC
│   │   ├── mock.py                演示用素材源
│   │   ├── local.py               本地目录素材源
│   │   └── capcut_web.py          剪映云空间素材源
│   ├── media/                     媒体处理层
│   │   ├── probe.py              素材探测（ffprobe→mp4box→fallback）
│   │   ├── mp4box.py             纯 Python MP4 解析器
│   │   ├── frames.py             FFmpeg 帧级分析（灰度统计/场景检测/JPEG抽取）
│   │   └── quality.py            画质打分模型
│   ├── analysis/                  镜头分析层
│   │   ├── base.py               ShotAnalyzer ABC
│   │   ├── heuristic.py          启发式分析器（默认通道）
│   │   └── vlm.py                VLM 分析器（可选增强）
│   ├── creative/                  创意生成层
│   │   ├── copywriter.py         文案引擎（模板+LLM+合规过滤）
│   │   ├── music.py              配乐匹配引擎
│   │   └── brand.py              品牌套件（样式管理）
│   ├── timeline/                  时间线编排层
│   │   └── orchestrator.py       选片决策大脑（纯逻辑）
│   ├── draft/                     草稿生成层
│   │   ├── schema.py             剪映草稿 schema 常量与工厂
│   │   ├── builder.py            草稿生成器
│   │   └── validator.py          草稿一致性校验器
│   ├── render/                    渲染脚本层
│   │   └── ffmpeg_script.py      FFmpeg 命令导出（PS+Bash双版）
│   └── web/                       Web 操作台
│       ├── server.py             纯标准库 HTTP 服务
│       └── static/index.html     操作台前端页面
├── tests/                         测试（unittest）
├── scripts/                       辅助脚本
├── assets/music/                  内置音乐素材
└── workspace/                     运行时工作区
    ├── projects/                  每项目的中间产物
    └── draft_output/             草稿输出（剪映目录不可用时）
```

---

## 九、总结

AutoCut Agent 是一个**工程上极为克制**的项目。它的核心设计理念是「零必装依赖 + 能力可降级」：核心流水线仅依赖 Python 标准库即可跑通端到端，FFmpeg/LLM/VLM/Playwright 每一项都是可选增强，缺失时自动降级到次优方案而非中断。

最值得称道的技术决策：

1. **纯 Python 灰度统计**替代 numpy/PIL/OpenCV 做帧级画面分析——用 FFmpeg 把帧降采样成灰度字节，在 Python 里算亮度/清晰度/稳定度，够用且零依赖
2. **双通道镜头分析**——启发式保证兜底，VLM 覆盖语义，继承关系让切分逻辑复用，失败时无缝回退
3. **草稿 Schema 版本适配层**——把剪映版本相关字段集中成 profile，生成器只依赖 profile 不硬编码版本号
4. **时间线编排器作为纯逻辑决策大脑**——可完全脱离 AI/剪映单测，所有决策写入 selection_report 可复核
5. **告警折叠机制**——同类告警达到阈值时折叠成「计数+原因+怎么办」，避免刷屏埋掉真正重要的问题
