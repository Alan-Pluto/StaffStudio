# 03 - 任务编排与 Harness v2 引擎

> 本文面向刚入职的硕士应届生，帮助你从零理解 StaffStudio 的核心执行引擎。
> 读完本文后，你应该能回答面试中关于"任务编排""幂等执行""崩溃恢复"等高频问题。

---

## 1. 总览

### 1.1 Harness v2 是什么

Harness v2 是 StaffStudio 的**核心执行引擎**——一个**持久化、租约隔离、基于 TaskFrame 的 Agent 执行流水线**。

你可以把它理解为一个"调度中心 + 执行器"的组合体：

```
用户消息 ──▶ HarnessV2Engine.run()
              │
              ├─ 1. 加锁 & 获取租约（防并发）
              ├─ 2. 幂等认领 Turn（防重复执行）
              ├─ 3. 规划 TurnPlan（LLM 决策）
              ├─ 4. 编译 & 执行 TaskFrame（Agent 循环）
              └─ 5. 生成回复 & 异步记忆捕获
```

### 1.2 核心设计目标

| 目标 | 含义 | 实现手段 |
|------|------|----------|
| **Exactly-once** | 同一条请求不会被执行两次 | `client_turn_id` + SHA-256 摘要 |
| **崩溃恢复** | 进程重启后不丢状态 | AgentLoop checkpoint + 后台 Sweeper |
| **并发安全** | 同一会话不会被两个 worker 同时执行 | Session Lock + Session Lease (fencing) |
| **预算可控** | 单次 Turn 不会无限消耗 token | `max_actions` 预算 + 延迟机制 |

---

## 2. 架构全景

### 2.1 核心文件地图

```
backend/app/core/
├── harness_v2_engine.py      # 外层引擎，~2000 行，整个 run() 流水线
├── harness_agent.py          # 内层 Agent 循环 (HarnessTaskAgent)
├── task_frame_store.py       # TaskFrame 持久化调度 (TaskFrameStore)
├── task_request_compiler.py  # 将 Frame 编译为可执行的 TaskRequirement
├── harness_session_lease.py  # 会话级互斥租约 (900s TTL)
├── harness_turn_store.py     # Exactly-once Turn 收据
├── harness_recovery.py       # 后台 Sweeper，回收孤儿执行
├── harness_capability_invoker.py  # 能力执行桥接器
├── turn_planner.py           # TurnPlanner，LLM 规划阶段
└── slot_hydration_policy.py  # Slot 注水策略
```

### 2.2 分层架构图

```
┌─────────────────────────────────────────────────────────────┐
│                     HarnessV2Engine                         │
│  (外层编排: 锁/租约/Turn认领/规划/帧调度/回复生成)            │
├──────────────┬──────────────────────────────────────────────┤
│              │                                              │
│  TurnPlanner │   TaskFrameStore    TaskRequestCompiler       │
│  (LLM决策)   │   (帧生命周期)       (编译需求)               │
│              │                                              │
├──────────────┴──────────────────────────────────────────────┤
│                   HarnessTaskAgent                          │
│  (内层循环: 迭代式工具调用, checkpoint/resume)                │
├─────────────────────────────────────────────────────────────┤
│              HarnessCapabilityInvoker                       │
│  (能力执行: 工具/SOP/知识库/文件, 冻结清单授权)               │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 执行流水线：HarnessV2Engine.run()

`run()` 方法是整个引擎的入口，下面逐步拆解。

### 3.1 完整流水线

```
用户消息到达
    │
    ▼
┌──────────────────────────────────────────────────────┐
│ Step 1: Session Lock (进程内 threading.Lock)          │
│   acquire_harness_session(session_id)                │
│   作用: 同一进程内同一会话串行执行                      │
└──────────────────────┬───────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────┐
│ Step 2: Session Lease (DB 级租约, 900s TTL)           │
│   session_leases.acquire(session)                    │
│   作用: 跨进程/跨 worker 的互斥, owner fencing        │
└──────────────────────┬───────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────┐
│ Step 3: Turn Claim (Exactly-once 收据)               │
│   turn_store.claim(session, request)                 │
│   - 计算 SHA-256 摘要 (排除 session_id/client_turn_id)│
│   - 若已存在相同摘要且已完成 → 直接 replay 响应        │
│   - 若正在执行中 → 拒绝重复请求                        │
└──────────────────────┬───────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────┐
│ Step 4: 持久化用户消息                                 │
│   owner._append_message("user", request.message)     │
│   turn_store.bind_user_message(turn_record, msg_id)  │
└──────────────────────┬───────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────┐
│ Step 5: Skill Projection (SOP 投影)                   │
│   expand_visible_sops() → 可执行的 SOP 列表            │
│   discoverable_sops()  → 可路由的 SOP 列表             │
│   作用: 决定本轮哪些 SOP 对 Planner 可见               │
└──────────────────────┬───────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────┐
│ Step 6: Memory Recall (记忆召回)                      │
│   MemoryService.context_memories(tenant, user, agent) │
│   作用: 把用户历史记忆注入上下文                         │
└──────────────────────┬───────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────┐
│ Step 7: Conversation Context (对话上下文)              │
│   _conversation_context(session)                     │
│   滑动窗口 + 摘要压缩, 控制 token 用量                  │
└──────────────────────┬───────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────┐
│ Step 8: Planning (TurnPlanner.plan())                │
│   - LLM 分析用户意图                                   │
│   - 输出 TurnPlan: decision + task_frames[]           │
│   - 每个 TaskFrame 包含: kind, skill_id, step_id,    │
│     decision, requirements, depends_on_task_ids 等    │
└──────────────────────┬───────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────┐
│ Step 9: Slot Hydration (槽位注水)                     │
│   SlotHydrationPolicy.hydrate_plan()                 │
│   从记忆中提取已知字段, 预填到 TaskFrame 的 slot_hints  │
└──────────────────────┬───────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────┐
│ Step 10: Team Dispatch (团队分发, 可选)                │
│   若 frame.execution_target == "team_member":         │
│     publish_team_planner_frames() → 写入 TeamTask     │
│   本地只保留非 team_member 的帧继续执行                  │
└──────────────────────┬───────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────┐
│ Step 11: Frame Execution Loop                        │
│   for each TaskFrame record:                         │
│     ├─ 检查依赖 (dependencies_satisfied?)             │
│     ├─ 激活帧 (_activate_frame)                      │
│     ├─ 构建能力清单 (CapabilityManifestBuilder)       │
│     ├─ 编译需求 (TaskRequestCompiler.compile)         │
│     ├─ 运行 Agent (HarnessTaskAgent.run)             │
│     └─ 应用结果 (_apply_step_result)                 │
└──────────────────────┬───────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────┐
│ Step 12: Response Generation                         │
│   response_generator.generate()                      │
│   综合所有帧结果, 生成最终回复                          │
└──────────────────────┬───────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────┐
│ Step 13: Memory Capture (异步, 可选)                  │
│   _enqueue_memory_capture()                          │
│   后台提取本轮值得记住的信息                            │
└──────────────────────┬───────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────┐
│ Step 14: Turn Completion                             │
│   turn_store.complete(turn_record, response)         │
│   持久化响应, 用于幂等 replay                          │
└──────────────────────────────────────────────────────┘
```

### 3.2 关键代码片段

**Session Lease 获取与续期：**

```python
# harness_v2_engine.py:218
self.session_lease = self.session_leases.acquire(session)

# 在长时间执行中定期续期:
def _renew_session_lease(self) -> None:
    self.session_leases.renew(self.session_lease)
    self.turn_store.renew(self.turn_record)
    self.db.commit()
```

**Turn 幂等认领：**

```python
# harness_turn_store.py:45-77
def claim(self, session, request) -> HarnessTurnClaim:
    client_turn_id = str(request.client_turn_id or "").strip()
    if not client_turn_id:
        return HarnessTurnClaim(record=None)
    digest = _request_digest(request)  # SHA-256
    existing = self._find(session, client_turn_id)
    if existing is not None:
        return self._existing_claim(existing, digest)
    # ... 创建新 record
```

---

## 4. 内层 Agent 循环：HarnessTaskAgent

### 4.1 核心定位

`HarnessTaskAgent` 是执行层的"工人"。它接收一个 `TaskRequirement`（编译后的任务需求），在一个**迭代式工具调用循环**中完成任务。

```
TaskRequirement ──▶ HarnessTaskAgent.run()
                       │
                       ▼
                  ┌──────────┐
                  │ 构建 LLM  │
                  │  Prompt   │
                  └────┬─────┘
                       ▼
                  ┌──────────┐     ┌─────────────┐
                  │ LLM 输出  │────▶│ 解析 Action  │
                  │ (JSON)   │     └──────┬──────┘
                  └──────────┘            │
                                    ┌─────┴─────┐
                                    │           │
                              action=tool   action=finish
                                    │           │
                                    ▼           ▼
                              ┌──────────┐  ┌────────┐
                              │ 调用工具  │  │ 返回结果│
                              │ 记录结果  │  └────────┘
                              └────┬─────┘
                                   │
                                   ▼
                              继续下一轮迭代
                              (直到 finish 或 budget 耗尽)
```

### 4.2 HarnessAction 协议

每次 LLM 必须输出一个符合 schema 的 JSON：

```python
class HarnessAction(BaseModel):
    action: Literal["tool", "finish"]
    tool_name: str | None = None        # action=tool 时必填
    arguments: dict[str, Any] = {}
    status: Literal["completed", "awaiting_user", "handoff", "failed"] | None = None
    reply_fragment: str = ""            # action=finish 时的回复片段
    slot_updates: dict[str, Any] = {}   # 槽位更新
    next_step_id: str | None = None     # SOP 下一步
    task_summary: str = ""
    structured_result: Any | None = None
```

### 4.3 关键特性

#### Checkpoint / Resume

Agent 循环支持断点续传。每次迭代结束时，checkpoint 会持久化到 `HarnessAgentLoopRecord`：

```python
result.loop_checkpoint = {
    "version": 1,
    "task_frame_id": requirement.task_frame_id,
    "step_id": current_step_id,
    "transcript": _transcript_for_model(transcript),
    "citations": citations[-20:],
    "evidence_results": evidence_results[-10:],
    "capability_results": capability_results[-20:],
    "artifacts": artifacts[-20:],
    "recent_task_summaries": recent_task_summaries[-8:],
    # ...
}
```

当进程崩溃后重启，recovery sweeper 会将 frame 状态恢复为 `queued`，下次执行时从 checkpoint 恢复。

#### Protocol Repair（协议修复）

当 LLM 输出不符合 `HarnessAction` schema 时，引擎会尝试一次修复：

```python
for protocol_attempt in range(2):
    raw = _generate_harness_action_json(client, system_prompt, payload)
    try:
        actions = _harness_actions_from_raw(raw)
    except (ValidationError, ValueError) as exc:
        if protocol_attempt == 0:
            # 第一次失败: 注入修复提示, 重试
            payload = {
                **payload,
                "protocol_repair": {
                    "message": "上一次输出不符合 HarnessAction Schema...",
                    "invalid_output": raw,
                    "validation_error": str(exc),
                },
            }
            continue
        raise  # 第二次仍失败, 放弃
```

#### 知识检索预算

每个 TaskFrame 最多成功调用 `knowledge_search` **2 次**：

```python
MAX_SUCCESSFUL_KNOWLEDGE_SEARCHES_PER_TASK = 2

# 超预算后返回:
{
    "success": False,
    "error": {
        "code": "KNOWLEDGE_SEARCH_BUDGET_EXHAUSTED",
        "message": "当前 TaskFrame 已完成两次有效知识检索...",
    },
}
```

#### 不可重试动作追踪

如果某个工具调用因确定性原因失败（如参数无效），引擎会记录其签名，防止 LLM 重复提交相同调用：

```python
non_retryable_action_signatures: set[str] = set()
# ...
if action_signature in non_retryable_action_signatures:
    result = {
        "success": False,
        "error": {"code": "NON_RETRYABLE_ACTION_REPEATED", ...},
    }
```

#### Action Budget（动作预算）

`max_actions` 参数控制单次 `run()` 的最大迭代次数，范围 1-100，默认由 Agent 配置决定（通常 32）。预算耗尽时返回 `status="action_budget"`，frame 保持 `queued` 状态，下一轮 Turn 可以继续执行。

---

## 5. TaskFrame 生命周期

### 5.1 状态机

```
                  ┌────────────┐
                  │   queued   │ ◀── 初始状态 / 恢复后重新排队
                  └─────┬──────┘
                        │ mark_running() (获取 lease)
                        ▼
                  ┌────────────┐
                  │   running  │ ◀── 正在执行
                  └─────┬──────┘
                        │ finish_frame()
            ┌───────────┼───────────┬──────────────┐
            ▼           ▼           ▼              ▼
      ┌──────────┐ ┌─────────┐ ┌──────────┐ ┌──────────┐
      │completed │ │ failed  │ │awaiting  │ │ deferred │
      │          │ │         │ │_user     │ │(queued)  │
      └──────────┘ └─────────┘ └──────────┘ └──────────┘
```

### 5.2 依赖管理

TaskFrame 支持通过 `depends_on_task_ids` 声明前置依赖：

```python
# task_frame_store.py
def dependencies_satisfied(self, row, records) -> bool:
    dependency_ids = list(row.depends_on_json or [])
    if not dependency_ids:
        return True
    # 检查所有依赖帧是否已完成
    ...
```

如果依赖未满足，frame 会被延迟（`defer_for_dependencies`），并在依赖完成后自动释放：

```python
# harness_v2_engine.py:587-608
if row.status == "completed":
    released = self.store.ready_dependency_frames(
        session,
        exclude_task_ids=known_record_ids,
    )
    if released:
        records.extend(released)
        self.events.record(..., "task_frame_dependencies_released", ...)
```

### 5.3 租约认领（Lease-based Claiming）

TaskFrame 使用**乐观并发控制**：

```python
# task_frame_store.py:226-257
def mark_running(self, row):
    lease_owner = new_id("hlease")
    attempt_no = max(0, int(row.attempt_no or 0)) + 1
    statement = (
        update(HarnessTaskFrameRecord)
        .where(
            HarnessTaskFrameRecord.id == row.id,
            HarnessTaskFrameRecord.status == "queued",
            HarnessTaskFrameRecord.state_version == expected_version,
        )
        .values(
            status="running",
            attempt_no=attempt_no,
            lease_owner=lease_owner,
            lease_expires_at=now + timedelta(seconds=FRAME_LEASE_SECONDS),
            state_version=expected_version + 1,
        )
    )
    result = self.db.exec(statement)
    if result.rowcount != 1:
        raise TaskFrameClaimConflict(...)
```

关键点：
- `state_version` 做乐观锁，防止并发修改
- `lease_owner` 唯一标识执行者
- `lease_expires_at` 900 秒过期，防止僵尸占用

### 5.4 预算延迟

当 Turn 的 `remaining_turn_actions` 耗尽时，剩余帧被延迟：

```python
# harness_v2_engine.py:483-498
if remaining_turn_actions <= 0:
    deferred_rows = records[record_index:]
    self.store.defer_for_action_budget(deferred_rows)
    self.events.record(..., "turn_action_budget_exhausted", ...)
    break
```

---

## 6. 并发与安全机制

### 6.1 三层防护体系

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: Session Lock (进程内)                               │
│   threading.Lock, 同一进程内同一 session 串行                 │
│   文件: harness_session_lock.py                              │
├─────────────────────────────────────────────────────────────┤
│ Layer 2: Session Lease (跨进程, DB 级)                       │
│   900s TTL, owner fencing, 过期可被抢占                       │
│   文件: harness_session_lease.py                             │
├─────────────────────────────────────────────────────────────┤
│ Layer 3: Turn Store (幂等, Exactly-once)                     │
│   client_turn_id + SHA-256 digest, 防重复执行                 │
│   文件: harness_turn_store.py                                │
└─────────────────────────────────────────────────────────────┘
```

### 6.2 Session Lease 详解

```python
# 获取租约
def acquire(self, session) -> HarnessSessionLeaseToken:
    row = HarnessSessionLeaseRecord(
        lease_owner=new_id("hsleaseowner"),
        lease_expires_at=now + timedelta(seconds=900),
    )
    self.db.add(row)
    try:
        self.db.commit()  # 唯一约束: 同一 session 只能有一条有效租约
    except IntegrityError:
        self.db.rollback()
        return self._take_expired(session)  # 抢占过期租约

# 续期 (带 fencing)
def renew(self, lease) -> None:
    result = self.db.exec(
        update(HarnessSessionLeaseRecord)
        .where(
            HarnessSessionLeaseRecord.lease_owner == lease.lease_owner,
            HarnessSessionLeaseRecord.lease_expires_at > now,
        )
        .values(lease_expires_at=now + timedelta(seconds=900))
    )
    if result.rowcount != 1:
        raise HarnessSessionLeaseLost(...)  # 被其他 worker 抢占了
```

### 6.3 Recovery Sweeper（崩溃恢复）

后台线程每 60 秒扫描一次，回收孤儿执行：

```python
# harness_recovery.py
SWEEP_INTERVAL_SECONDS = 60.0

def recover_orphan_harness_runs(db, *, startup=False, now=None):
    # 1. 找到所有 status="running" 的 run/frame/turn
    # 2. 过滤出 lease 已过期 (或 startup 模式下全部) 的记录
    # 3. 将 orphan run 标记为 "abandoned"
    # 4. 将 orphan frame 恢复为 "queued" (保留 checkpoint)
    # 5. 将 orphan turn 标记为 "failed"
    # 6. 清理 session lease, 允许下一轮执行
    # 7. 追加恢复提示消息
```

关键设计决策：
- **保留 checkpoint**：崩溃前已执行的步骤不会丢失
- **不 replay**：已中断的 attempt 不会被重放，而是标记为 `interrupted`
- **释放 session**：清理死租约，让下一轮 Turn 可以正常进入

### 6.4 Action Budget 配置

```python
# 通过 Agent 配置控制
max_actions = max(1, min(int(max_actions), 100))
# 默认 32, 范围 1-100
```

---

## 7. TaskRequestCompiler：从帧到需求

`TaskRequestCompiler` 负责将一个 `PlannedTaskFrame` + 当前 SOP 节点编译为一个可执行的 `TaskRequirement`：

```python
class TaskRequirement(BaseModel):
    task_frame_id: str
    kind: Literal["sop", "conversation"]
    goal: str                          # 任务目标
    requirements: list[str]            # 子需求列表
    sop_context: dict[str, Any]        # SOP 节点上下文
    required_slots: list[str]          # 待收集字段
    known_slots: dict[str, Any]        # 已知字段
    completion_criteria: list[str]     # 完成标准
    required_capability_names: list[str]    # 必须调用的能力
    required_knowledge_base_ids: list[str]  # 必须检索的知识库
    capability_manifest: CapabilityManifest  # 冻结的能力清单
    memory_projection: list[dict]      # 记忆投影
    prior_task_results: list[dict]     # 前置帧结果
    attachments: list[dict]            # 附件描述
```

编译过程：

1. **定位当前 SOP 节点** → 提取 instruction、expected_fields
2. **计算 required_slots** → expected_fields 中尚未收集的字段
3. **组装 requirements** → 节点指令 + 字段补齐 + 帧级需求
4. **组装 completion_criteria** → 字段收集 + SOP 目标 + 强制能力
5. **冻结 capability_manifest** → 只暴露当前节点授权的能力

---

## 8. HarnessCapabilityInvoker：能力执行桥

`HarnessCapabilityInvoker` 是 Agent 与外部能力之间的桥梁，核心职责：

1. **授权检查**：只允许调用冻结清单中的能力
2. **渐进式展开**：`capability_describe` 可以激活新能力（渐进式发现）
3. **幂等执行**：通过 `ToolReplayPolicy` 防止重复执行
4. **租约续期**：每次调用前检查执行租约是否有效

```python
class HarnessCapabilityInvoker:
    def invoke(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        # 1. 检查取消
        self._raise_if_cancelled()
        # 2. 续期执行租约
        if callable(self.ensure_execution_lease):
            self.ensure_execution_lease()
        # 3. 授权检查
        descriptor = self._descriptors.get(name)
        if descriptor is None:
            return _failure("TOOL_NOT_AVAILABLE", ...)
        if name not in self._activated_names:
            return _failure("CAPABILITY_NOT_ACTIVATED", ...)
        # 4. 幂等检查
        replayed = self._replay_or_block(logical_action_key)
        if replayed is not None:
            return replayed
        # 5. 执行能力
        ...
```

---

## 9. 数据流总览

```
用户消息
    │
    ▼
ChatTurnRequest ──▶ HarnessV2Engine.run()
    │                    │
    │                    ├──▶ TurnPlanner.plan()
    │                    │        │
    │                    │        ▼
    │                    │    TurnPlan
    │                    │    ├── decision: "start_new_task" | "continue_active" | ...
    │                    │    └── task_frames[]:
    │                    │         ├── kind: "sop" | "conversation"
    │                    │         ├── target_skill_id
    │                    │         ├── target_step_id
    │                    │         ├── requirements[]
    │                    │         └── depends_on_task_ids[]
    │                    │
    │                    ├──▶ TaskFrameStore.persist_plan()
    │                    │        │
    │                    │        ▼
    │                    │    HarnessTaskFrameRecord[]
    │                    │
    │                    ├──▶ for each frame:
    │                    │    │
    │                    │    ├──▶ TaskRequestCompiler.compile()
    │                    │    │         │
    │                    │    │         ▼
    │                    │    │    TaskRequirement
    │                    │    │
    │                    │    ├──▶ HarnessTaskAgent.run()
    │                    │    │         │
    │                    │    │         ▼ (迭代循环)
    │                    │    │    HarnessCapabilityInvoker.invoke()
    │                    │    │         │
    │                    │    │         ▼
    │                    │    │    TaskExecutionResult
    │                    │    │
    │                    │    └──▶ _apply_step_result()
    │                    │
    │                    └──▶ response_generator.generate()
    │                              │
    │                              ▼
    │                         ChatTurnResponse
    │
    ▼
返回给用户
```

---

## 10. 面试题

### Q1: Harness v2 如何保证 Exactly-once 语义？

**答：** 通过 `HarnessTurnStore` 实现。每个请求携带 `client_turn_id`，引擎计算请求体的 SHA-256 摘要（排除 `session_id` 和 `client_turn_id` 本身）存入 `HarnessTurnRecord`。

- 若相同 `client_turn_id` 已存在且摘要匹配、状态为 `completed` → 直接 replay 已保存的响应
- 若摘要不同 → 抛 `HarnessTurnConflict`，拒绝执行
- 若正在执行中 → 拒绝重复请求

这确保了即使客户端重试或网络重传，同一请求也只会被执行一次。

### Q2: 如果执行过程中进程崩溃了，系统如何恢复？

**答：** 三层机制协同工作：

1. **AgentLoop Checkpoint**：每次迭代结束后，transcript、citations、artifacts 等状态持久化到 `HarnessAgentLoopRecord.checkpoint_json`
2. **Recovery Sweeper**：后台线程每 60 秒扫描 `status="running"` 的记录，将 lease 过期的 run/frame 标记为 abandoned/queued
3. **Checkpoint 恢复**：下次执行同一 frame 时，`HarnessTaskAgent.run()` 从 checkpoint 恢复 transcript 和中间状态，继续执行

关键设计：**不 replay 已中断的 attempt**，而是将 frame 恢复为 `queued`，让下一轮 Turn 重新认领。

### Q3: Session Lease 和 Session Lock 有什么区别？为什么需要两层？

**答：**

| | Session Lock | Session Lease |
|---|---|---|
| 作用域 | 进程内 | 跨进程/跨 worker |
| 实现 | `threading.Lock` | DB 记录 + TTL |
| 目的 | 防止同一进程内并发 | 防止多 worker 并发 |
| 过期 | 不超时（阻塞等待） | 900s TTL，可被抢占 |

需要两层的原因：`threading.Lock` 无法跨进程工作（如多 worker 部署），而 DB 租约无法处理同一进程内的线程竞争。两者互补，形成完整的并发防护。

### Q4: TaskFrame 的依赖管理是如何工作的？

**答：** 每个 TaskFrame 可以声明 `depends_on_task_ids`。执行循环中：

1. 按拓扑序排列所有帧
2. 执行到某帧时，先检查 `dependencies_satisfied()`
3. 若依赖未满足 → `defer_for_dependencies()`，记录 `DEPENDENCY_WAITING` 事件
4. 当前置帧完成时 → `ready_dependency_frames()` 释放被阻塞的帧
5. 释放的帧加入执行队列继续处理

支持的激活条件（团队任务中更丰富）：`all_succeeded`、`any_succeeded`、`minimum_succeeded`、`all_terminal`。

### Q5: Action Budget 机制是如何防止无限执行的？

**答：** 两层预算控制：

1. **Turn 级预算**：`remaining_turn_actions` 控制一轮 Turn 中所有帧共享的动作总数。每执行一个帧，扣除其 `action_count`。耗尽时，剩余帧被 `defer_for_action_budget()`，保持 `queued` 状态等下一轮
2. **Frame 级预算**：`max_actions` 控制单个 `HarnessTaskAgent.run()` 的最大迭代次数（1-100，默认 32）。耗尽时返回 `status="action_budget"`，帧保持 `queued` 可恢复

### Q6: Protocol Repair 机制是如何工作的？

**答：** 当 LLM 输出的 JSON 不符合 `HarnessAction` schema 时：

1. 第一次失败：注入 `protocol_repair` 上下文（包含错误信息和正确的 schema 示例），让 LLM 修正格式
2. 第二次仍失败：抛出异常，帧标记为 `failed`

这种"给一次修正机会"的策略，显著降低了因格式问题导致的任务失败率。

### Q7: Capability Manifest 的"冻结-渐进展开"设计有什么好处？

**答：** 编译 `TaskRequirement` 时，能力清单是**冻结**的——只暴露当前 SOP 节点授权的能力。但 Agent 可以通过调用 `capability_describe` 工具**渐进式地发现并激活**新能力。

好处：
- **安全**：Agent 不能调用未授权的能力
- **高效**：不需要一开始就加载所有能力的完整 schema
- **灵活**：Agent 可以根据任务需要动态发现相关能力

### Q8: 为什么需要 `non_retryable_action_signatures`？

**答：** 防止 LLM 陷入死循环。当某个工具调用因确定性原因失败（如参数格式错误、资源不存在），相同参数重试必然再次失败。引擎记录失败调用的签名（工具名 + 参数哈希），如果 LLM 再次提交相同签名，直接返回错误而不实际执行。这迫使 LLM 更换参数或策略，而不是无限重试。
