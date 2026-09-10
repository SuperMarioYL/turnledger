[English](README.en.md) | **简体中文**

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/hero-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/hero-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/hero-dark.svg">
  <img src="assets/presentation/hero-light.svg" width="960" alt="TurnLedger — Keep the interruption state for continuation.">
</picture>

**TurnLedger 保存一轮输出被打断时的 cursor 与 payload，支持内存或 SQLite 存储，再把挂起状态交还给 adapter 继续处理。**

`Python 3.10+` · [MIT](LICENSE) · [GitHub](https://github.com/SuperMarioYL/turnledger) · [网站](https://turnledger.lei6393.com)

## 为什么需要它

长输出被打断时，应用需要知道哪些内容已经发出、哪些中间参数还没处理完。把这个位置显式保存，可以让接入方选择从剩余内容继续，而不是丢失状态后重播。账本保存状态，不会替任意模型或音频后端恢复内部执行。

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/process-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/process-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/process-dark.svg">
  <img src="assets/presentation/process-light.svg" width="960" alt="Resume four known text chunks">
</picture>

## 架构

TurnCursor 表示 token/step/sub_step，Ledger 缓存 report 的当前位置，suspend 写 yielding 记录，resume 取最近未恢复记录并写 resuming。Store 有 InMemory 与 SQLite 实现。文本 adapter 使用返回索引继续已知 token 列表；可选 LiveKit adapter 将事件接到账本，进一步恢复生成/播放仍由应用负责。

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/architecture-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/architecture-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/architecture-dark.svg">
  <img src="assets/presentation/architecture-light.svg" width="960" alt="Application-owned state, reusable persistence">
</picture>

## 安装

需要 Python 3.10+。安装可能联网；下面的文本与 SQLite 示例无需音频、LiveKit 或模型服务。

```bash
git clone https://github.com/SuperMarioYL/turnledger.git
cd turnledger
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## 快速开始

```bash
python examples/presentation_demo.py
```

四个构造文本片段 A/B/C/D 在索引 2 处中断。真实文本 adapter 的续接输出为 ABCD，显式 restart 对照为 ABABCD。另一轮将 cursor=2 和 remaining=CD 保存到 SQLite，关闭后重开并恢复，历史含 yielding/resuming，随后不可再次恢复同一挂起项。

## 用法

```python
from turnledger import InMemoryLedger, TurnCursor
ledger = InMemoryLedger()
ledger.report("turn-1", cursor=TurnCursor(token_index=2), payload={"remaining": "CD"})
ledger.suspend("turn-1")
entry = ledger.resume("turn-1")
print(entry.cursor.token_index, entry.payload)
```

交互式文本演示可用 turnledger demo --contrast。自定义后端读取 entry 并决定如何继续。需要显式关闭数据库时，可组合 Ledger(SQLiteStore(path))，完成后调用 store.close()，如本轮示例。

SQLite 审计日志可从命令行直接读取——`turnledger demo --store sqlite` 结束后，用 `turnledger history` 查看该轮的挂起/恢复记录：

```bash
turnledger demo --store sqlite   # 交互演示，账本写入 turnledger.db
turnledger history turn-demo     # 打印该轮全部 LedgerEntry（seq/phase/cursor/时间戳）
turnledger history turn-demo --json --db path/to/turnledger.db
```

## 能力与集成

| 接口 | 职责 |
|---|---|
| TurnCursor / payload | 接入方定义位置与恢复数据 |
| InMemoryLedger | 进程内状态 |
| SQLiteStore / SQLiteLedger | 本地保存挂起/恢复记录 |
| TextStreamAgent | 从已知文本片段列表继续 |
| LiveKitLedgerAgent | 可选事件钩子和 resumed_entry |
| attach_ledger | 订阅中断；恢复钩子需接入方处理 |

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/integrations-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/integrations-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/integrations-dark.svg">
  <img src="assets/presentation/integrations-light.svg" width="960" alt="Current ledger and adapter surfaces">
</picture>

## 配置与边界

核心不需要服务配置。使用不同 turn_id 区分话轮；payload 必须能由选择的存储序列化。report 只更新进程内当前位置，尚未 suspend 的位置不会因此自动持久化。

resume 追加恢复记录，也更新原 yielding 条目的 resumed_at，因此不能把它称为不可变审计日志。核心使用同步本地存储，不提供跨节点一致性。

LiveKit extra 只安装可选依赖，仍需确认版本与适配器导入接口匹配。其 token_index 来自语音计数，不是精确音频采样点；它提供恢复状态，不自动重建已取消的语音/模型流。

## 运行记录

v0.1.0 的文本模拟器与真实 SQLite 关闭/重开。restart 是同一示例自定义的对照模式，不代表其他语音产品的默认行为；没有实际音频或模型请求。

[输入、命令和完整输出](docs/demo-results.json)

[保留的历史终端录屏](assets/demo.gif) · [录制脚本](docs/demo.tape)。本轮示例以以上可重放记录为准。

## 路线图

- [x] cursor/payload 与挂起、恢复 API。
- [x] 内存、SQLite 和文本对照示例。
- [x] 可选 LiveKit 事件接入代码。
- [ ] 更多框架的完整恢复流程与实测。
- [ ] 跨语言协议和多节点后端。

当前没有已交付的 Cloud 席位套餐或多区域服务。

## 开发与许可证

```bash
python -m pip install -e ".[test]"
python -m pytest
```

协议见 turnledger/protocol.py；文本 adapter 在 turnledger/adapters/text_stream.py。

[MIT](LICENSE) · [Issues](https://github.com/SuperMarioYL/turnledger/issues)
