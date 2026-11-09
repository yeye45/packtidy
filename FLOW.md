# packtidy 完整流程

从一块乱盘到整理完成，中间经过六个阶段。每个阶段有明确的输入、输出和停止条件。

---

## 全景图

```
                        ┌─────────────┐
                        │  一块盘      │
                        └──────┬──────┘
                               │
        ┌──────────────────────┴──────────────────────┐
        │                                             │
        ▼                                             ▼
   ┌─────────┐                                  ┌──────────┐
   │  S1     │  每个一级目录派一个子代理            │ 读规则包  │
   │  判定    │  「这是什么、该做什么」              │ rules/   │
   └────┬────┘                                  └────┬─────┘
        │                                             │
        │  probe/*.json                                │
        ▼                                             │
   ┌─────────┐                                        │
   │  S2     │  split/enter 的节点继续往下拆            │
   │  清点    │  「里面有几个最小单元」                 │
   └────┬────┘                                        │
        │                                             │
        │  units_*.json                               │
        ▼                                             │
   ┌─────────┐                                        │
   │  S3     │  「这些单元该归成几组」                 │
   │  归组    │                                        │
   └────┬────┘                                        │
        │                                             │
        │  group_*.json                               │
        ▼                                             ▼
   ┌──────────────────────────────────────────────────┐
   │  S4 审批                                          │
   │                                                   │
   │   代码算建议 ──┬── 能定 ──→ 直接给建议              │
   │               │                                   │
   │               └── 定不了 ──→ 问题包 ──→ AI        │
   │                                        │          │
   │                                        ├─ 猜想    │
   │                                        ├─ 验证    │
   │                                        └─ 确认    │
   │                                        │          │
   │                                        ▼          │
   │                                     answers.json  │
   └────────────────────┬──────────────────────────────┘
                        │
                        │  decisions.json + UI
                        ▼
                  ┌──────────┐
                  │  人看界面  │  只改标出来的几项
                  └────┬─────┘
                       │
                       ▼
   ┌──────────────────────────────────────┐
   │  S5 执行                              │
   │                                       │
   │   预演 → 分批 → 写 journal            │
   │   任何一步可 undo                      │
   └────────────────────┬─────────────────┘
                        │
                        ▼
   ┌──────────────────────────────────────┐
   │  S6 覆盖检查                          │
   │  每个节点都处理了吗？漏了哪个？          │
   └──────────────────────────────────────┘
```

---

## 前置：读规则包

```bash
python core/rules.py show        # 看当前生效什么规则
python core/rules.py check       # 校验格式
```

规则包定义三样东西：

```
protected     哪些目录名不能动（windows / program files / node_modules）
nestedAllow   受保护区内哪些位置仍可整理（Downloads / Cache / Temp）
categories    内容类型 → 关键词 → 建议目录名
mapping       类型 → 你盘上实际叫什么（这项空着，由 policy.json 填）
```

**为什么规则要分离**：判据会改，改错了要能回滚；规则包可独立发布，不必跟着代码版本走。

---

## S1 判定 —— 这是什么

**一个目录一个子代理。** 不合并。

### 派活

```
给每个一级目录派一个探针子代理，任务是：
  ① 读名字，形成假设
  ② 进目录验证假设
  ③ 给出动作：keep / enter / split / rename / merge / ask
```

### 判定顺序（不做跳跃）

```
① 用户声明？        有 → 验证内容吻合否
② 名字有信息？      有 → 假设 → 验证 → 吻合则按名字方向
③ 名字无信息？      必须打开 → 自成一类则改名
④ 数字/时间戳名？   先解码 → 对得上就保留
```

### 名字可信度

| 形态 | 可信度 | 要验证什么 |
|---|---|---|
| 专名（作品名） | 高 | 进一层确认主题一致 |
| 软件/引擎名 | 高 | 确认是安装目录就停 |
| 用途词（Downloads） | 中 | 内容是否真是这个用途 |
| 疑似分类（Compressed） | 中 | **粒度对不对** |
| 泛化词（资料/杂） | 低 | 进一层 + 抽样 |
| 占位符（新建文件夹） | 零 | 全靠内容 |
| 数字/随机串 | 零 | 先解码再靠内容 |

### 输出

`probe/*.json`，每个目录一个：

```json
{
  "path": "/disk/某目录",
  "name_probe": {"hypothesis": "看起来是下载落地目录", "name_confidence": "medium"},
  "actual": {"what": "混着 5 类东西", "content_matches_name": false},
  "is_unit": "container",
  "action": "split",
  "evidence": ["顶层 42 项，类型跨 6 种", "无共同主题"],
  "confidence": "high"
}
```

---

## S2 清点 —— 里面有几个最小单元

**只对 `split` / `enter` 的节点做。**

### 什么是一个单元

按形态定，不靠语义推断：

```
游戏/软件     整个目录 = 1 单元（exe + 数据 + 存档拆了就废）
已归类作品集   文件夹 = 1 单元
散落文件      每个文件 = 1 单元
容器         内部每个子项按上面判
```

### 三条补充

```
同批到达 ≠ 同一组     三个目录同秒创建，主题不同 = 3 个单元
判同类看用途不看名字   两个"素材"目录，一个外拍一个爬取 = 2 个单元
拿不准 → uncertain   不下钻，不猜
```

### 可代码化的信号

```bash
python core/signals.py /path/to/dir --json
```

输出事实，不做判断：

```
A 结构绑定    unity-layout / vm-layout / engine-payload / save-binding
B 序列连续    sequence（前缀+连续编号）/ volume-parts（分卷完整性）
C 到达聚集    batch-arrival（同秒创建比例）/ time-span
D 独立性      composition（类型构成）/ is-shell（套壳）/ reparse（Junction）
```

**信号是事实，判断在 `references/judgment.md`。**

### 输出

`units_*.json`：

```json
{
  "path": "/disk/某目录",
  "total_units": 62,
  "confirmed_units": [...],
  "loose_files_as_units": [...],
  "sub_containers": [...],
  "uncertain": [...]
}
```

---

## S3 归组 —— 这些单元该归成几组

**组 = 去向相同的单元集合。** 不是"主题相似"。

### 归组能发现的四类关系

这是 S3 最核心的价值——**单看一个目录永远发现不了**：

| 关系 | 特征 | 动作 |
|---|---|---|
| **集数互补** | 同一作品拆成两个目录，编号正好互补 | 合并，否则两半都残 |
| **多季多版本** | 同 IP 的多个季度 / 多套 BD | 取超集，其余标注 |
| **跨层重复** | 顶层与嵌套目录各一份 | 比哈希后留一份 |
| **形态类聚** | 来源形态一致但作品各异 | 只聚合，不合并 |

### 必须跨子目录

一个目录的三个子目录可能只是三次搬运的产物，名字反映"什么时候搬来的"而不是"装了什么"。

**按子目录分别归组，永远凑不出正确的组合。**

### 判据优先级

```
先按内容 id 判    workflow 根节点 id / 文件哈希 / 内部结构
再按元数据判      created / creator / 版本号
最后才按名字判    名字像但 id 不同的一律不并
```

### 输出

`group_*.json`：

```json
{
  "path": "/disk/某目录",
  "groups": [{
    "name": "某作品合集",
    "units": ["a", "b", "c"],
    "relation": "multi-version",
    "why": "同一 IP 的三个来源，内容有重叠",
    "confidence": "high"
  }],
  "solo_units": [...],
  "uncertain": [...]
}
```

---

## S4 审批 —— 每组去哪

**这是最复杂的一步，分两半：代码能算的、只有 AI 能判断的。**

### 代码能算的

```
content_shape()     扫扩展名分布 → installer / media / image / code / document
name_stem()         从组名提取词根
规则匹配            组名关键词 → categories
冲突检测            内容形态 vs 目标类别是否矛盾
propose_new_categories()  从未匹配的组里发现候选类别
```

### 代码算不出来的

```
这个组到底是什么东西      内容形态只说明"是什么文件"，不说明"是什么"
该叫什么名字              词根提出来了，但"某工具"该叫"装机工具"？
该归到哪一类              盘上没有现成的类，该建哪个
名字和内容冲突时信谁       名字说音乐，内容是 exe
```

**这四种一律标 `needsCheck`，交给 AI。**

### AI 介入

```bash
# 生成问题清单
python core/decisions.py --root /path --need-ai > questions.json
```

问题包只放事实：

```json
{
  "group": "RJ123456 某作",
  "parent": "下载",
  "shape": {"kind": "installer", "dominant": ".exe", "ratio": 1.0},
  "sampleFiles": ["a.exe", "b.exe"],
  "suggestion": {"mode": "keep",
                 "evidence": ["内容以 installer 为主"]},
  "question": "规则包中没有匹配项，但内容是 installer。这些是什么？",
  "requiredOutput": {
    "flow": "猜想 → 验证 → 确认，三步都要走",
    "fields": {
      "hypothesis": "最初认为这是什么",
      "verification": {"method": "...", "findings": "...", "disproved": [...]},
      "confirmation": {"holds": true, "confidence": "high",
                       "why": "...", "residualRisk": "..."}
    }
  }
}
```

### AI 走三步

**① 猜想**

先写下认为它是什么，**再去验**。

**② 验证 —— 先本地，再查外部**

| 猜想 | 本地证据 | 要联网吗 |
|---|---|---|
| 这是成品音乐 | 里面是 .mp3 还是 .exe | 不要 |
| 这是一组分卷 | 卷号连不连续 | 不要 |
| 这是同一作品两份 | 文件数、体积、哈希 | 不要 |
| 这是安装包不是游戏 | exe + 数据 + 存档 | 不要 |
| 这是哪个作品 | 需要外部资料 | **要** |
| 有没有成人分级 | 本地无法判定 | 可能要 |

**外网只在"需要知道作品叫什么"时才有用。**

验证的核心是**找能推翻自己的证据**：

```json
"verification": {
  "method": "先看内容形态：.exe 占 89%，是安装体不是媒体文件。
             再看样例文件名：VEditor_v6.exe、初音未来V4X.exe——
             都是安装程序",
  "findings": "本地证据已足够推翻'成品音乐'",
  "disproved": ["按名字归为音乐是错的——它是工具不是成品"]
}
```

**③ 确认**

```json
"confirmation": {
  "holds": true,
  "confidence": "high",
  "why": "内容形态与文件名两条本地证据一致指向'工具'",
  "residualRisk": "没确认这些声库对应哪个版本，但不影响归类"
}
```

**`residualRisk` 很重要** —— 承认边界，不是"我很确定"就完了。

### 回填

```bash
python core/decisions.py --root /path --answers ai.json
```

代码做什么：

```
✓ 原样记录 AI 交来的三步
✓ 检查三步有没有给全（完整性，不是质量）
✓ 没给全的列进报告，仍待确认
✓ 尊重 AI 说"站不住"——holds:false 就保持待确认
✗ 不检查 verification 写得够不够详细
✗ 不因为 confidence 高就放行
✗ 不替 AI 降级——记录原值
```

**为什么不由代码检查质量**：`method: "查了"` 和 `method: "查了 DLsite"` 代码分不出哪个更认真。"查得够不够"依赖具体上下文，只有 AI 知道它面对的是什么情况。

### 人在这里介入

```bash
python packtidy.py ui /path --open
```

界面只做一件事：**把需要人看的挑出来，其余默认填好。**

```
        ┌────────────────────────┐
        │          3             │
        │    组需要你看一眼       │
        │                        │
        │  53 组保持原位          │
        │  12 组移到已有目录       │
        │   3 组建议新建          │
        └────────────────────────┘

  ┌─ 需要你确认  3 项 ────────────────────┐
  │ 某组名                                │
  │ 依据…                    [选择 ▾]     │
  └───────────────────────────────────────┘

  ┌─ 可能的候选类别  2 个 ────────────────┐
  │ 某工具  3 组共享这一特征（形态一致）    │
  │ 内容形态：installer                    │
  │ 成员：工具A · 工具B · 工具C            │
  └───────────────────────────────────────┘

  建议删除             8 项  ›    ← 折叠
  移到别处            34 项  ›    ← 折叠
  保持不动             7 项  ›    ← 折叠
```

**人看到的是推理链，不是一句结论。**

---

## S5 执行

### 预演

```bash
python packtidy.py apply /path --actions plan.tsv --dry-run
```

预演结果先全部列出来，再执行剩下的。

### 预检（全部拦在动手之前）

```
路径出 root          → 拦
命中 PACKAGED 名字表  → 拦
命中 SECRET/FOREIGN/WORKSPACE/NO_TOUCH/ASK → 拦
跨盘（未显式允许）    → 拦
rename 的 dst 含分隔符 → 拦
```

### 执行

```bash
python packtidy.py apply /path --actions plan.tsv
```

**动作类型只有三种**（没有删除）：

```
move     移文件/目录到目标
rename   改名
mkdir    建目录
```

### journal

每步写一条，追加，永不改写：

```
seq  run_id  ts  op  before  after  reason  status
```

**撤销也是操作，也写日志**，所以撤销本身可再撤销。

### 撤销

```bash
python packtidy.py undo --journal journal.tsv --last --dry-run
python packtidy.py undo --journal journal.tsv --run-id run-xxx
```

四种情况：

| 情况 | 行为 |
|---|---|
| 目标不在 | 跳过并记录，不报错 |
| 原位置已有同名 | **不覆盖**，记 conflict，留给人 |
| rename 撤销时原名被占 | 改用 `<原名>_undone<扩展名>` |
| mkdir 撤销时目录非空 | 保留，记 skip |

### 中断恢复

```bash
python packtidy.py recover /path
```

执行到一半崩了，先恢复再继续。**不恢复就继续修改会留下不一致状态。**

---

## S6 覆盖检查

**每个阶段结束后必跑。**

```bash
python packtidy.py coverage /path
```

### 它检查三件事

```
S1  每个一级目录有没有探针判定
S2  每个 split/enter 节点有没有单元清点
S3  每个有清点的节点有没有归组
```

### 实测它抓出的三类遗漏

```
① 改名导致探针结果失效
   目录改名后 probe/*.json 的 path 还是旧的
   → coverage 认为"从未探过"

② 文件命名撞车
   units_TempH.json (path=/Temp H, 376 单元)
   units_tempH.json (path=/tempH,  4 单元)
   → Windows 大小写不敏感，结果互相覆盖

③ 新建目录没进流程
   手动新建了顶层区，没派探针
```

**一次真实漏检**：一个 700GB 量级的节点在 S3 阶段被漏掉，因为它的单元文件名带空格，和另一个只差大小写的目录撞了。**coverage 跑完才发现的。**

---

## 回归验证

```bash
python core/verify.py run cases/ --verbose
python core/verify.py coverage /path
```

**验的是"判断准不准"，不是"函数对不对"。**

函数可以全对，组合起来的判断仍可能错——VOCALOID 那个案例里，`name_stem` 和 `content_shape` 都没问题，是"信名字还是信内容"这个组合决策错了。

### 覆盖统计比通过率有用

```
总组数        22
已匹配        8  (36%)     ← 36% 说明规则包覆盖不足
未匹配        0  (0%)
需人工/AI     3  (14%)

未匹配的组（这些是规则包该补的）:
  某工具A · 某工具B · ...
```

---

## 标记

`_organize/markers.tsv`：

| kind | 语义 | 影响 |
|---|---|---|
| `FOREIGN` | 他人资产 | **从整理视野中完全消失**：不探测、不比较、不作为合并候选 |
| `WORKSPACE` | 未整理完 | 跳过，且不报"未整理" |
| `NO_TOUCH` | 明确不动 | 唯一免验证的例外 |
| `ASK` | 待用户回答 | 跳过，进待问清单 |
| `SECRET` | 凭据或隐私 | 不移动、不复制、不进 journal 路径 |
| `NOTE` | 纯备注 | 不影响动作 |

**skill 无法区分"乱"和"还没整理完"** —— 两者在文件系统上长得一模一样，区别只在人的意图里。

---

## 清理收押区（删除前的强制流程）

移进 `_待删除/` 不等于删除。

### 按证据强度分三组

```
A 组 · 证据确凿      同内容多份、字节级比对通过
B 组 · 用户明示      用户说过"这个不要了"
C 组 · 判断后删除    不属于当前主题、看着像垃圾、但没直接证据
```

**三组必须分开列，分开确认。**

### C 组必须让用户看到具体内容

```
20181110_155135.mp4   2分14秒   QQ 群成员照片树轮播
20190628_101539.mp4   4分56秒   演示「我爱十八班.bat」
20190218_174741.mp4   损坏      无 moov，不可播放
```

**用户看到这些描述才知道那是什么。只给文件名等于没给。**

### 实现约束

```
× 不用 Remove-Item 直接删
✓ 移到 _待删除/ 后停下，报告清单
✓ 由用户自己删，或明确说"删掉 A 组"
✓ 删除不进 journal —— 删了没法撤销，记了也没用
```

**`executor.py` 没有删除能力，这是刻意的。**

---

## 边界清单

### 明确不做

```
不删除用户原有文件
不预建分类目录等填
不改用户已有文件夹的名字（除了零信息名）
不在证据不足时硬给结论
不把"重复"当"可以自己删"
不为孤件建顶层目录
不合并"看起来像"的东西
```

### 遇到疑似涉及未成年人的性内容

**立即停止该路径的一切操作。**

```
× 不移动、不复制、不改名、不归类
× 不为其创建任何目录或标记
× 不纳入统计
× 不打开、不预览
✓ 在报告中单独列出路径
✓ 建议用户立即删除
```

**这不是"敏感内容"。这是违法的东西。与成人内容的处理方式不同。**

---

## 命令速查

```bash
# 查看
python core/rules.py show              规则包内容
python packtidy.py scan /path          看信号
python packtidy.py coverage /path      覆盖检查

# 决策
python core/decisions.py --root /path --need-ai    生成 AI 问题
python core/decisions.py --root /path --answers ai.json   回填
python packtidy.py ui /path --open      人看界面

# 执行
python packtidy.py apply /path --actions plan.tsv --dry-run
python packtidy.py apply /path --actions plan.tsv
python packtidy.py undo --journal j.tsv --last
python packtidy.py recover /path

# 验证
python core/verify.py run cases/
python core/verify.py coverage /path
```
