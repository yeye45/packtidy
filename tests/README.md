# 测试

## 运行

```bash
python -m pytest tests/ -v
# 或不用 pytest：
python -B -m unittest discover -s tests -v
```

## 测试什么

**signals.py 只输出事实，所以可以断言。** 这是把判据代码化的好处——
不用"看模型判断得对不对"，直接测函数返回值。

```python
# 合成一个 Unity 目录布局
d = tmpdir
(d / "game.exe").write(b"MZ")
(d / "game_Data").mkdir()
# 断言信号命中
assert signals.analyze(str(d))["signals"]["unity-layout"] is True
```

## 测试清单

| 测试 | 断言 |
|---|---|
| Unity 布局 | `unity-layout == True`，evidence 含 exe 与 _Data 对 |
| 虚拟磁盘 | `vm-layout == True`，`vm-disk-count` 正确 |
| 连续序列 | 编号范围正确、coverage 在 0–1，分辨率不是集数 |
| 序列有缺口 | `missing` 非空，但仍算 1 个单元 |
| 套壳 | `is-shell == True` |
| Junction | `reparse-count > 0`，不跟进 |
| 空目录 | `empty == True` |
| Hidden 目录 | 必须被枚举到（`os.scandir` 默认包含） |
| 单文件 | `file-count == 1`，不由它推断主题 |
| 保护标记 | 父子路径、NOTE、正斜杠、Windows 短路径不能解除保护 |
| 路径与引用 | 链接不能越界、同前缀目录不能误替换、移动和撤销同步嵌套路径 |
| 覆盖检查 | 嵌套节点漏阶段必须失败、BOM JSON 可读取、坏产物不能被当作完整 |
| 执行与撤销 | 内容不覆盖、重复撤销幂等、撤销可重做、序号范围不反复切换 |
| 中断恢复 | 准备日志失败、移动后中断、引用部分更新、镜像较新、标记双写中断 |
| 入口兼容 | 本机有 PowerShell 时实际验证 5.1/7 脚本及含中文/方括号路径 |

## 不测什么

- **不测"这个目录该不该拆"。** 那是 policy 和人的决定，不是代码。
- **不测 AI 的语义判断。** 探针的输出质量靠评分表，不靠单元测试。
- **不测真实盘。** 测试用合成布局，不依赖任何特定机器。

## 回归用例

从实际踩过的坑里提炼，每条能复现一次错判：

```
1. 零信息名文件读头      8MB 无扩展名 → 可能是固件，不是垃圾
2. 同秒创建但主题不同     batch-arrival=true 不足以判"一组"
3. 名字像但 id 不同       workflow 文件名相似，根节点 id 不同 → 不是同一套
4. 大小同但精度不同       fp32 vs fp16 同名模型 → 不是重复
5. 同名不同源             两个 Downloads 内容零重叠 → 不合并
6. 大小写撞文件名         TempH / temph 归一化为文件名时不得互相覆盖
7. Hidden 属性           不加 -Force 整类内容消失
8. Junction 跟进         23 个链接导致 330 个游戏数成 660
```

**每加一条判据，加一个对应的回归用例。** 判据和测试是一体的。
