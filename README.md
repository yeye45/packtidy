# packtidy

**顺着结构切分，而不是按扩展名硬套。**

传统整理工具把 `.jpg` 归图片、`.exe` 归程序——这会把一个 Unity 游戏的
可执行体、依赖库和数据目录拆到三个地方，游戏当场失效。

packtidy 换一种做法：先判断「这些文件是不是一个不可分割的整体」，
找到本来就存在的边界，再决定怎么归类。拿不准就报出来，不猜。

分阶段的文件整理 skill，**只用于划分和归并最小单元**，不负责"整理好看"。

## 它做什么

```
S1  判定      每个目录是什么，该做什么动作
S2  清点      每个目录里有几个最小单元
S3  归组      这些单元该归成几组
S4  审批      每组去哪
S5  执行      移动/改名，全部可回滚
S6  覆盖检查  确认没有节点被跳过
```

每个阶段的产物都是结构化 JSON/TSV，可复核、可测试、可累积。

## 它不做什么

- **不删除用户原有文件或目录。** 空目录只报告；撤销允许移除本次新建、身份匹配且仍为空的目录。
- **不为整理好看而动结构。** 目标是"以后找得到"。
- **不猜。** 拿不准就 `uncertain`，报给人。
- **不把「乱」和「还没整理完」当同一件事。** 后者由用户标记为 `WORKSPACE`。

## 核心概念

### 最小单元

按形态定边界，不靠语义推断：

```
游戏/软件     整个目录 = 1 单元（exe + 数据 + 存档绑定）
已归类作品集  文件夹 = 1 单元
散落文件      每个文件 = 1 单元
容器         内部每个子项按上面判
```

### 归组

**组 = 去向相同的单元集合。**

归组能发现四类关系，这些关系单看一个目录永远看不到：

| 关系 | 例子 |
|---|---|
| 集数互补 | 同一部剧被拆成两个目录，1-4 + 7-12 |
| 多季多版本 | 同 IP 的多个季度 / 多套 BD |
| 跨层重复 | 顶层与嵌套目录各一份 |
| 形态类聚 | 来源形态一致但作品各异 |

## 安装

```bash
git clone <repo> packtidy
```

**Windows**：PowerShell 5.1+ 和 Python 3.9+。
PowerShell 脚本使用 UTF-8 BOM，兼容 Windows PowerShell 5.1 和 PowerShell 7。
`python` 必须在 PATH 中，且需要 Python 3.9+，无第三方必需依赖。

**macOS / Linux**：可以直接调用 Python 工具及 `core/executor.py`；也可用 PowerShell 7 的入口。
文件修改要求系统和文件系统支持原子“不覆盖”改名；不支持时停止，不退回可能覆盖目标的接口。
当前完整集成验证在 Windows 完成，未在 macOS / Linux 实机运行。

## 快速开始

统一入口 `packtidy.py`，七个子命令对应六个阶段：

```bash
# 看盘上一级目录有什么（零成本，只读）
python packtidy.py coverage /mnt/disk

# 采集某个目录的信号（只读，输出事实）
python packtidy.py scan /mnt/disk/some-dir --json

# 从探针结果生成待批清单
python packtidy.py plan /mnt/disk --dry-run

# 生成决策界面（浏览器打开）
python packtidy.py ui /mnt/disk --open

# 执行 / 撤销 / 中断恢复
python packtidy.py apply /mnt/disk --actions plan.tsv --dry-run
python packtidy.py undo --journal /mnt/disk/_organize/journal.tsv --last --dry-run
python packtidy.py recover /mnt/disk
```

也可以直接用模块：`python core/signals.py <dir> --json`

**注意**：`plan` 和 `ui` 读取 `_organize/probe/*.json`——那些文件由 AI 代理执行
`references/probe-task.md` 等任务时产出。没有探针结果时，这两个命令会报「没有归组结果」。

## 一个完整的例子

```bash
# 1. 先看有没有遗漏（每次阶段结束都该跑）
python packtidy.py coverage /mnt/disk
#   → 列出哪些节点没有探针、哪些没清点、哪些没归组

# 2. 看某个目录到底是什么
python packtidy.py scan /mnt/disk/新建文件夹
#   → [结构绑定] Unity 布局 exe + _data/
#   → [到达聚集] 73% 同秒 (2025-01-24 18:18:28)
#   → [构成] 6 文件 / 1 目录 / 2 种类型

# 3. 生成界面，默认值已填好，只改标黄的几项
python packtidy.py ui /mnt/disk --open

# 4. 先预演，再执行
python packtidy.py apply /mnt/disk --actions plan.tsv --dry-run
python packtidy.py apply /mnt/disk --actions plan.tsv

# 5. 不满意就撤
python packtidy.py undo --journal /mnt/disk/_organize/journal.tsv --last
```

## 动作表格式

审批完成后用四列 TSV 执行。`move` 的 dst 是目录，`rename` 的 dst 是新文件名，
`mkdir` 的 dst 是绝对路径（各列必须用制表符分隔）：

```
op      src                     dst                     reason
move    /mnt/disk/下载/课程      /mnt/disk/学习           用户已批准
rename  /mnt/disk/新建文件夹     照片备份                 已确认内容
mkdir   -                       /mnt/disk/归档           下载暂存区拆分
```

也支持 PowerShell 封装：

```powershell
pwsh -File scripts/apply.ps1 -Root G:\ -Actions actions.tsv -DryRun
pwsh -File scripts/undo.ps1 -Journal journal.tsv -Last -DryRun
pwsh -File scripts/recover.ps1 -Root G:\
```

**`split` 不会自动展开**——必须先按最小单元归组、生成明确的动作表。
理由见 `references/judgment.md`：按扩展名拆分必然拆散引擎资源。

## 目录结构

```
packtidy.py                 统一入口（scan/coverage/plan/apply/undo/recover/ui）
SKILL.md                    流程与规则
policy.example.json         自动批准策略示例（复制为 policy.json 后按你的盘改）
core/
  executor.py               执行、撤销、事务恢复；PowerShell 共用此实现
  safety.py                 路径、保护标记及链接检查
  signals.py                可代码化的判据 → 输出事实 JSON
  coverage.py               覆盖检查：有没有节点被跳过
  decisions.py              S4 建议：每组该去哪，含新建分类的归属规划
  ui.py                     生成决策界面（单文件 HTML，跟随系统主题）
references/
  probe-task.md             S1 判定任务
  unit-inventory.md         S2 清点任务
  grouping-task.md          S3 归组任务
  judgment.md               信号 → 单元数的判断规则
scripts/
  apply.ps1                 执行（经 journal）
  undo.ps1                  逆序撤销
  recover.ps1               恢复未完成操作或镜像中完整的日志后缀
  common.ps1                Python 调用桥接
  journal.ps1               日志
  markers.ps1               标记（FOREIGN/WORKSPACE/SECRET…）
  peek_archive.py           读压缩包条目表（不解压）
  export-feedback.py        本地生成脱敏摘要（不联网、不上传）
```

## 安全设计

**先记录后修改。** `journal.tsv` 保留八列追加式 TSV，操作先写 pending、完成后写 done。
reason 列使用 JSON 保存人工依据、事务编号、文件身份和逆操作关联。
`_organize/transactions/` 与本机镜像同时保存可恢复的引用变更；中断时禁止继续修改，先运行 recover。
每个日志路径有独立的本机镜像目录，避免不同盘的记录混在一起。

**按操作状态撤销。** 撤销通过原始 seq 关联日志，重复撤销跳过已经撤销的项；对撤销产生的 run 再执行 undo 即可重做。
文件对象已被替换或修改时保留现场；冲突不覆盖。原有八列日志仍可读取/列出，但缺少身份信息的旧动作不会被自动撤销。
`undo -Seq N` 仅选择编号不大于 N 的原始正向动作，不把历史撤销/重做再次混入范围；需要重做时指定撤销产生的 RunId 或使用 Last。
保护标记的双写也有独立恢复记录，写入中断时会阻止后续整理，recover 会完成已记录的标记更新。

**删除永不自动。** `policy.json` 里删除默认关闭。

**凭据隔离。** `SECRET` 标记的文件及其父目录不移动，拦截结果不输出凭据路径；NOTE 不能解除保护。
源、目标及沿途目录的链接、Junction、重解析点都会被拒绝，目录内部包含这些项也不能整体移动。
凭据可能藏在任何地方——随机名目录、备份归档深处、微信年月子目录。
建议把凭据扫描当独立步骤，不靠探针顺带发现。

**遇到疑似涉及未成年人的性内容**：立即停止对该路径的一切操作，
不移动、不归类、不统计，只在报告中列出路径。

## 已知限制

- **不做内容语义判断。** 它不知道"这张照片拍的是什么"，只知道文件结构。
- **跨平台尚未完整验证。** Windows PowerShell 5.1/7 已做集成测试，macOS/Linux 接口未实机验证。
- **只支持同卷移动。** 旧参数 `-AllowCrossDrive` 会明确拒绝；不支持用户文件全量复制/删除。
- **日志不是备份。** 对象被外部替换、日志历史分歧或其他无法证明的状态需要人工核对，不猜测恢复。
- **故障验证范围。** 测试覆盖进程中断和模拟写入失败；没有做真实断电、硬件损坏或外置盘强制拔出测试，不承诺硬件故障下的完整恢复。
- **并发边界。** 同一根目录的工具操作共用锁；整理期间应暂停其他会修改同一路径的下载、同步或手工操作。工具不会覆盖竞争出现的目标。
- **大目录慢。** 几十万文件的目录，`signals.py` 可能要跑几分钟。
- **不检测损坏。** 截断的文件、不完整的分卷，只标记不修复。

## 测试

```bash
python -B -m unittest discover -s tests -v
```

包含信号、路径别名、保护标记、嵌套覆盖、引用同步、执行/撤销/重做、日志故障和中断恢复的合成目录测试；有 PowerShell 时也实际验证脚本入口。

## License

MIT
