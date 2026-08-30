# 04 - 多 Agent 协作

> 本文面向刚入职的硕士应届生，帮助你理解 StaffStudio 中多个 Agent 如何协同工作。
> 读完本文后，你应该能回答面试中关于"多 Agent 协作""任务竞标""黑板系统""状态机"等高频问题。

---

## 1. 总览

### 1.1 两种协作形态

StaffStudio 中有两种多 Agent 协作方式：

```
┌────────────────────────────────────────────────────────────────────┐
│  形态一: 渠道自动路由 (Channel Auto-routing)                        │
│                                                                    │
│  多个 Agent 共享一个渠道(如企业微信群), 根据用户消息内容自动          │
│  路由到最合适的 Agent 处理。                                         │
│                                                                    │
│  用户消息 ──▶ Router ──▶ Agent A (匹配)                            │
│                      └─▶ Agent B (不匹配)                          │
│                      └─▶ Agent C (不匹配)                          │
├────────────────────────────────────────────────────────────────────┤
│  形态二: 团队协作 (Team Collaboration)                              │
│                                                                    │
│  一个 TL(团队领导) + 多个成员 Agent, 围绕任务进行分解、竞标、        │
│  执行、验收的完整协作流程。                                          │
│                                                                    │
│  用户需求 ──▶ TL 分解任务 ──▶ 成员竞标 ──▶ 成员执行 ──▶ TL 验收    │
└────────────────────────────────────────────────────────────────────┘
```

本文重点讲解**团队协作**形态，因为它涉及更丰富的系统设计。

### 1.2 核心文件

```
backend/app/teams/
├── service.py     # 团队模型、状态机、竞标、黑板、解析函数
└── wakeup.py      # 唤醒事件调度、成员执行、TL 验收、竞标流程
```

---

## 2. 团队模型

### 2.1 数据库模型

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│    Team      │────▶│  TeamMember   │────▶│ AgentProfile  │
│              │     │              │     │              │
│ id           │     │ team_id      │     │ id           │
│ tenant_id    │     │ agent_id     │     │ name         │
│ name         │     │ role         │     │ metadata_json│
│ description  │     │   (leader/   │     │  (expertise  │
│ owner_user_id│     │    member)   │     │   _tags)     │
│ config_json  │     └──────────────┘     └──────────────┘
│ status       │
└──────┬───────┘
       │
       ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   TeamTask   │────▶│TeamTaskEvent │     │ TeamTaskBid   │
│              │     │              │     │              │
│ id           │     │ task_id      │     │ task_id      │
│ team_id      │     │ event_type   │     │ agent_id     │
│ team_run_id  │     │ actor_type   │     │ round        │
│ title        │     │ actor_id     │     │ content      │
│ description  │     │ payload_json │     │ score        │
│ status       │     └──────────────┘     │ kind         │
│ assignee_    │                          │ hp           │
│  agent_id    │     ┌──────────────┐     └──────────────┘
│ depends_on_  │     │TeamBlackboard│
│  task_ids    │     │  Entry       │     ┌──────────────┐
│ activation_  │     │              │     │  TeamWake     │
│  condition   │     │ team_id      │     │  Event       │
│ report_json  │     │ content      │     │              │
│ review_json  │     │ tags_json    │     │ team_id      │
│ version      │     │ source_type  │     │ target_agent │
└──────────────┘     │ citation_json│     │ trigger_type │
                     └──────────────┘     │ status       │
                                          └──────────────┘
```

### 2.2 团队结构

```
┌─────────────────────────────────────────┐
│              Team                        │
│                                         │
│   ┌─────────┐                           │
│   │  TL     │  role="leader"            │
│   │ (1人)   │  负责: 分解任务、打分、     │
│   │         │       验收、裁决中标        │
│   └────┬────┘                           │
│        │                                │
│   ┌────┴────────────────────┐           │
│   │     Members (多人)       │           │
│   │  role="member"          │           │
│   │  每个成员是一个           │           │
│   │  AgentProfile           │           │
│   │  拥有 expertise_tags    │           │
│   └─────────────────────────┘           │
└─────────────────────────────────────────┘
```

关键约束：
- 一个团队**至多一名 TL**（`set_leader()` 换任时自动将原 TL 降为 member）
- 每个成员对应一个 `AgentProfile`（有独立的 SOP、工具、知识库）
- `expertise_tags` 用于竞标候选筛选

---

## 3. 任务状态机

### 3.1 状态定义

```python
# teams/service.py:32-51
TASK_STATUSES = {
    "blocked",       # 被依赖阻塞
    "pending",       # 待执行（已分配或竞标完成）
    "bidding",       # 竞标中
    "in_progress",   # 成员执行中
    "review",        # 待 TL 验收
    "done",          # 已完成（终态）
    "rework",        # 被 TL 退回
    "escalated",     # 升级给人处理（终态）
}
```

### 3.2 状态转换图

```
                    ┌──────────┐
                    │ blocked  │
                    └────┬─────┘
                         │ 依赖满足
                         ▼
  ┌─────────┐     ┌──────────┐     ┌───────────┐
  │escalated│◀────│ pending  │────▶│  bidding   │
  └─────────┘     └────┬─────┘     └─────┬─────┘
       ▲               │                 │ 竞标完成
       │               │ 直接分配        │
       │               ▼                 │
       │         ┌──────────────┐        │
       │         │ in_progress  │◀───────┘
       │         └──────┬───────┘
       │                │ 提交报告
       │                ▼
       │         ┌──────────┐
       │         │  review  │
       │         └──┬───┬───┘
       │            │   │
       │     approve│   │rework
       │            ▼   ▼
       │    ┌──────┐ ┌───────┐
       │    │ done │ │rework │──▶ in_progress (重做)
       │    └──────┘ └───────┘
       │
       │  任何非终态均可 escalated
       └──── escalated
```

### 3.3 转换规则

```python
# teams/service.py:42-51
TASK_TRANSITIONS: dict[str, set[str]] = {
    "blocked":     {"pending", "escalated"},
    "pending":     {"bidding", "in_progress", "escalated"},
    "in_progress": {"review", "escalated"},
    "review":      {"done", "rework", "escalated"},
    "rework":      {"in_progress", "escalated"},
    "bidding":     {"pending", "escalated"},
    "done":        set(),          # 终态，不可转换
    "escalated":   set(),          # 终态，不可转换
}
```

### 3.4 apply_task_transition()

所有状态变更必须通过此函数，它负责：
1. 验证转换合法性
2. 更新任务状态和版本号
3. 写入审计事件

```python
# teams/service.py:494-530
def apply_task_transition(db, task, new_status, *, actor_type, actor_id, event_type, payload):
    if new_status not in TASK_STATUSES:
        raise TeamTaskTransitionError(f"未知任务状态: {new_status}")
    if new_status != task.status and new_status not in TASK_TRANSITIONS.get(task.status, set()):
        raise TeamTaskTransitionError(f"任务不允许从 {task.status} 流转到 {new_status}")
    previous = task.status
    task.status = new_status
    task.version += 1
    task.updated_at = utc_now()
    db.add(task)
    record_task_event(db, ..., event_type=event_type,
                      payload={"from_status": previous, "to_status": new_status, ...})
    return task
```

---

## 4. 任务竞标系统

### 4.1 竞标流程总览

当一个任务没有指定 `assignee_agent_id` 时，进入任务池由成员竞标：

```
TL 创建任务(无指定人)
    │
    ▼
pending ──▶ bidding
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ Round 1: 陈述阶段                                            │
│   1. select_bid_candidates() → 选最多 3 名候选               │
│   2. 各候选提交 bid (plan / cost / confidence)               │
│   3. TL 为每位候选打分 (0-10)                                │
│   4. 扣减 HP: HP = HP - (10 - score) × 3                   │
├─────────────────────────────────────────────────────────────┤
│ Round 2..N: 反驳阶段 (默认共 3 轮)                           │
│   1. 各存活候选看到其他候选上一轮发言                          │
│   2. 提交反驳/补强                                           │
│   3. TL 再次打分                                             │
│   4. HP 归零 → 淘汰                                         │
├─────────────────────────────────────────────────────────────┤
│ 最终轮: 裁决                                                 │
│   TL 从存活候选中选择中标者                                   │
│   输出 bid_award JSON 块                                     │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
bidding ──▶ pending ──▶ in_progress (中标者执行)
```

### 4.2 候选筛选

```python
# teams/service.py:770-795
def select_bid_candidates(db, team, task) -> list[str]:
    leader = get_team_leader(db, team.id)
    query = f"{task.title}\n{task.description or ''}".lower()
    scored = []
    for member in list_team_members(db, team.id):
        if member.agent_id == leader.agent_id:  # TL 不参与竞标
            continue
        agent = db.get(AgentProfile, member.agent_id)
        tags = [tag.lower() for tag in agent.metadata_json.get("expertise_tags", [])]
        score = sum(1 for tag in tags if tag and tag in query)
        scored.append((score, member.agent_id))
    scored.sort(key=lambda x: (-x[0], x[1]))
    positive = [aid for s, aid in scored if s > 0]
    pool = positive if positive else [aid for _, aid in scored]
    return pool[:BID_CANDIDATE_LIMIT]  # 最多 3 人
```

筛选逻辑：
1. 排除 TL
2. 按 `expertise_tags` 与任务标题/描述的子串重叠计分
3. 有正分数的优先；全 0 分时取所有非 TL 成员
4. 封顶 3 人

### 4.3 HP 血条机制

```python
# teams/service.py:739-758
BID_HP_INITIAL = 100       # 初始 HP
BID_HP_LOSS_PER_POINT = 3  # 每差 1 分扣 3 点 HP

def candidate_hp(bids):
    hp = {}
    for bid in sorted(bids, key=lambda b: (b.round, b.created_at)):
        if bid.score is None:
            continue
        current = hp.get(bid.agent_id, float(BID_HP_INITIAL))
        loss = max(0.0, 10.0 - bid.score) * BID_HP_LOSS_PER_POINT
        hp[bid.agent_id] = max(0.0, current - loss)
    return {agent_id: round(value) for agent_id, value in hp.items()}
```

示例：

```
Round 1:
  Agent A: score=8 → HP = 100 - (10-8)×3 = 94
  Agent B: score=6 → HP = 100 - (10-6)×3 = 88
  Agent C: score=3 → HP = 100 - (10-3)×3 = 79

Round 2:
  Agent A: score=9 → HP = 94 - (10-9)×3 = 91
  Agent B: score=4 → HP = 88 - (10-4)×3 = 70
  Agent C: score=1 → HP = 79 - (10-1)×3 = 52

Round 3 (裁决):
  存活: A(91), B(70), C(52) → TL 从中选中标者
```

### 4.4 竞标轮数配置

```python
# teams/service.py:761-767
DEFAULT_BID_REBUTTAL_ROUNDS = 3  # 1 陈述 + 2 反驳

def bid_rebuttal_rounds(team) -> int:
    config = team.config_json or {}
    return max(0, int(config.get("bid_rebuttal_rounds", DEFAULT_BID_REBUTTAL_ROUNDS)))
# 0 或 1 = 关闭辩论，陈述后直接裁决
```

### 4.5 JSON 解析函数

竞标系统依赖从 LLM 回复中提取结构化 JSON 块：

```python
# 解析成员竞标
def parse_bid(reply) -> dict | None:
    # {"bid": {"plan": "...", "estimated_cost": ..., "confidence": ...}}

# 解析 TL 打分
def parse_bid_scores(reply, candidate_ids) -> dict | None:
    # {"bid_scores": {"agent_id": {"score": 8.5, "rationale": "..."}}}

# 解析 TL 裁决
def parse_bid_award(reply, candidate_ids) -> dict | None:
    # {"bid_award": {"winner_agent_id": "...", "scores": {...}, "comment": "..."}}
```

所有解析函数都使用 `extract_json_blocks()` 从 ` ```json ``` ` 围栏代码块中提取，坏 JSON 直接跳过。

---

## 5. 黑板系统

### 5.1 什么是黑板

黑板是团队级的**共享知识板**。成员完成任务后可以将重要发现写入黑板，供其他成员和 TL 参考。

```
┌───────────────────────────────────────────────────────┐
│                   团队黑板                              │
│                                                       │
│  - [市场分析] 竞品 A 定价 $99/月, 功能覆盖 80%         │
│  - [技术方案] 推荐用 PostgreSQL 而非 MySQL              │
│  - [客户反馈] 用户最关心数据导出功能                     │
│                                                       │
│  每条记录包含: content + tags[] + citation(task_id)    │
└───────────────────────────────────────────────────────┘
```

### 5.2 写入流水线

```python
# teams/service.py:598-684
def write_blackboard_entries(db, *, team, entries, source_type, ...):
    """规范化 → 去重合并 → 结构化写入"""
```

流水线步骤：

```
原始条目
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 1: 内容规范化                                            │
│   normalize_blackboard_content(): 压缩所有空白为单空格         │
│   normalize_blackboard_tags(): 小写、去重、只保留字符串        │
├─────────────────────────────────────────────────────────────┤
│ Step 2: 批次内去重                                            │
│   同一批次中相同规范化内容只保留一条                            │
├─────────────────────────────────────────────────────────────┤
│ Step 3: 与既有条目比较                                        │
│   ├─ 完全相同 → 跳过                                          │
│   ├─ 新内容是既有条目的子串 → 跳过(已有更完整版本)              │
│   ├─ 新内容是既有条目的超集 → 合并更新既有条目                  │
│   └─ 无包含关系 → 新增条目                                    │
└─────────────────────────────────────────────────────────────┘
```

子串去重示例：

```
已有: "PostgreSQL 14 支持 JSON 路径查询"
新增: "PostgreSQL 14"                → 跳过(子串)
新增: "PostgreSQL 14 支持 JSON 路径查询和全文检索" → 合并更新已有条目
新增: "MySQL 8.0 支持窗口函数"       → 新增
```

### 5.3 上下文注入

```python
# teams/service.py:687-717
def blackboard_context_lines(db, team, query_text, *, limit=10) -> list[str]:
    """按 query 与条目 tags 的关键词重叠打分, top-K 注入"""
    rows = ... # 查询所有 active 条目
    def sort_key(entry):
        score = sum(1 for tag in entry.tags_json if tag in query.lower())
        return (-score, not entry.pinned, -entry.updated_at.timestamp())
    rows.sort(key=sort_key)
    return [format(entry) for entry in rows[:limit]]
```

注入时机：
- TL 分解任务时（`build_tl_chat_context()`）
- 成员执行任务时（`build_member_task_message()`）
- TL 验收时（`build_tl_review_message()`）
- 成员竞标时（`build_bid_request_message()`）

---

## 6. 任务依赖与激活

### 6.1 依赖声明

TL 在分解任务时可以声明依赖关系：

```json
{
  "team_tasks": [
    {"title": "市场调研", "task_id": "T1"},
    {"title": "竞品分析", "task_id": "T2", "depends_on_task_ids": ["T1"]},
    {"title": "方案撰写", "task_id": "T3", "depends_on_task_ids": ["T1", "T2"],
     "activation_condition": {"type": "all_succeeded"}}
  ]
}
```

### 6.2 激活条件类型

```python
# teams/service.py:152-196
def task_activation_state(db, task) -> str:
    """返回 "ready" / "blocked" / "impossible" """
    condition_type = condition.get("type", "all_succeeded")

    if condition_type == "all_succeeded":
        # 所有依赖都 done → ready
        # 有依赖终态但非 done → impossible
        # 否则 → blocked

    if condition_type == "any_succeeded":
        # 任一依赖 done → ready
        # 全部终态但无 done → impossible
        # 否则 → blocked

    if condition_type == "minimum_succeeded":
        # done 数 >= minimum → ready
        # done 数 + 剩余数 < minimum → impossible
        # 否则 → blocked

    if condition_type == "all_terminal":
        # 全部终态(done/escalated) → ready
        # 否则 → blocked
```

### 6.3 激活流程图

```
T1(无依赖) ──▶ 立即 ready ──▶ in_progress ──▶ done
                                                    │
T2(依赖T1) ──▶ blocked ──────────────────────▶ ready ──▶ in_progress
                                                         │
T3(依赖T1,T2; all_succeeded) ──▶ blocked ──────────────▶ ready
```

`activate_ready_tasks()` 在每次唤醒事件完成后统一调用，重新计算所有 blocked 任务的激活状态。

---

## 7. TL（Team Leader）工作流

### 7.1 TL 的核心职责

```
┌─────────────────────────────────────────────────────────────┐
│  TL 工作流                                                   │
│                                                             │
│  1. 需求分解                                                 │
│     parse_tl_task_assignments() → 解析 team_tasks JSON 块    │
│     输出: 任务列表(标题/描述/指派人/依赖/激活条件)              │
│                                                             │
│  2. 竞标管理                                                 │
│     - 候选筛选 → select_bid_candidates()                     │
│     - 每轮打分 → parse_bid_scores()                          │
│     - 最终裁决 → parse_bid_award()                           │
│                                                             │
│  3. 任务验收                                                 │
│     parse_tl_review() → 解析 team_review JSON 块             │
│     verdict: approve(通过) / rework(退回) / escalate(升级)    │
│     可选: blackboard_writes[] (认可的黑板条目)                 │
│                                                             │
│  4. 团队综合                                                 │
│     所有任务终态后, 汇总生成团队级报告                         │
└─────────────────────────────────────────────────────────────┘
```

### 7.2 TL 任务分解解析

```python
# teams/service.py:110-149
def parse_tl_task_assignments(reply) -> list[dict]:
    """从 TL 回复中提取 team_tasks JSON 块"""
    tasks = []
    for block in extract_json_blocks(reply):
        raw_tasks = block.get("team_tasks")
        if not isinstance(raw_tasks, list):
            continue
        for item in raw_tasks:
            task = {"title": item["title"]}
            # 可选字段:
            # - assignee_agent_id: 指定执行者(空=任务池竞标)
            # - description: 任务描述
            # - depends_on / depends_on_task_ids: 依赖
            # - activation_condition: 激活条件
            tasks.append(task)
    return tasks
```

### 7.3 TL 验收解析

```python
# teams/service.py:281-304
def parse_tl_review(reply) -> dict | None:
    """解析 TL 验收块"""
    # {"team_review": {
    #   "verdict": "approve" | "rework" | "escalate",
    #   "comment": "验收意见",
    #   "blackboard_writes": [{"content": "...", "tags": [...]}]  # 可选
    # }}
```

验收结论与状态映射：

```python
VERDICT_TARGET_STATUS = {
    "approve": "done",        # 通过 → 完成
    "rework": "rework",       # 退回 → 重做
    "escalate": "escalated",  # 升级 → 给人处理
}
```

### 7.4 团队上下文注入

```python
# teams/service.py:533-548
def team_roster_lines(db, team) -> list[str]:
    """花名册: agent_id, 名称, 角色, 能力标签"""
    # - agent_id=xxx 名称=小助手 角色=TL 能力标签=市场分析,数据分析
    # - agent_id=yyy 名称=写手   角色=成员 能力标签=文案撰写

# teams/service.py:551-569
def open_tasks_summary(db, team) -> list[str]:
    """未闭环任务摘要, 供 TL 了解当前进度"""
    # - task_id=xxx 标题=市场调研 状态=in_progress 负责人=yyy
```

---

## 8. 唤醒事件系统

### 8.1 唤醒事件类型

```python
# teams/wakeup.py
EXECUTION_WAKE_TYPES = {"task_assigned", "task_rework"}

# 所有唤醒类型:
# - task_assigned: 新任务分配给成员
# - task_rework:   任务被退回, 成员重做
# - task_report:   成员提交报告, TL 验收
# - bid_request:   竞标请求, 成员提交竞标
# - bid_judge:     竞标裁决, TL 打分/选中标者
# - team_synthesis: 团队综合, 生成最终报告
```

### 8.2 唤醒事件生命周期

```
                    ┌──────────┐
                    │ pending  │ ◀── 创建
                    └────┬─────┘
                         │ claim_wake_event() (原子认领)
                         ▼
                    ┌──────────┐
                    │ claimed  │ ◀── 后台线程执行中
                    └────┬─────┘        ↑ 心跳续期 (30s)
                         │              │
            ┌────────────┼────────────┐ │
            ▼            ▼            ▼ │
       ┌────────┐  ┌────────┐  ┌──────┐│
       │  done  │  │ failed │  │pending││ (超时未心跳,
       └────────┘  └────────┘  │(恢复) ││ 被 sweeper 回收)
                               └───────┘│
```

### 8.3 成员执行串行控制

```python
# teams/wakeup.py:722-731
DEFAULT_MEMBER_CONCURRENCY = 1  # 默认同一成员串行执行

def member_concurrency(team) -> int:
    config = team.config_json or {}
    return max(1, int(config.get("member_concurrency", DEFAULT_MEMBER_CONCURRENCY)))
```

双重额度判定：
1. **进程内计数**：`_member_slot_counts` 字典 + `_member_slot_guard` 锁
2. **DB 计数**：`_member_in_progress_count()` 查询 `status="in_progress"` 的任务数

当额度已满时，唤醒事件保持 `pending`，记录 `wake_queued` 审计，等当前任务终态后由 `_drain_member_queue()` 出队拉起。

### 8.4 崩溃恢复

```python
# teams/wakeup.py:476-513
def recover_orphaned_wake_events(db, *, lease_timeout_seconds=180.0):
    """回收超过租约且无心跳的 claimed 事件"""
    cutoff = now - timedelta(seconds=lease_timeout_seconds)
    rows = db.exec(
        select(TeamWakeEvent)
        .where(TeamWakeEvent.status == "claimed",
               TeamWakeEvent.updated_at < cutoff)
    ).all()
    for row in rows:
        # 原子更新: claimed → pending
        result = db.exec(update(TeamWakeEvent)
            .where(id=row.id, status="claimed", updated_at < cutoff)
            .values(status="pending", error=None))
```

配合 `dispatch_pending_wake_events()` 定期扫描并重新派发。

---

## 9. 完整协作流程

### 9.1 端到端流程图

```
用户: "帮我做一个新产品上线方案"
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│ Phase 1: TL 分解需求                                              │
│                                                                  │
│ TL 会话 (interaction_mode="team_tl")                              │
│   上下文注入: 花名册 + 未闭环任务 + 黑板 + 分解指令                 │
│   TL 输出:                                                        │
│   ```json                                                        │
│   {"team_tasks": [                                               │
│     {"title": "市场调研", "task_id": "T1"},                       │
│     {"title": "竞品分析", "task_id": "T2", "depends_on": ["T1"]},│
│     {"title": "上线方案", "task_id": "T3", "depends_on": ["T2"]} │
│   ]}                                                             │
│   ```                                                            │
│                                                                  │
│ → 创建 TeamTask × 3, T1=blocked(无依赖→ready), T2/T3=blocked     │
│ → T1 无 assignee → 进入竞标                                       │
└──────────────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│ Phase 2: 任务竞标 (T1)                                            │
│                                                                  │
│ T1: pending → bidding                                            │
│   1. select_bid_candidates() → [Agent A, Agent B, Agent C]       │
│   2. Round 1: 各成员提交 bid → TL 打分 → 扣 HP                    │
│   3. Round 2: 反驳轮 → TL 打分 → 扣 HP                           │
│   4. Round 3: TL 裁决 → Agent A 中标                              │
│                                                                  │
│ T1: bidding → pending (assignee=Agent A)                         │
└──────────────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│ Phase 3: 成员执行                                                  │
│                                                                  │
│ T1: pending → in_progress                                        │
│   唤醒事件: task_assigned → Agent A 的独立会话                      │
│   上下文注入: 任务描述 + 黑板                                       │
│   Agent A 在自己的 Harness v2 中执行(选 SOP/工具/知识库)            │
│   完成后: 提交报告 + 黑板建议                                      │
│                                                                  │
│ T1: in_progress → review                                         │
│   唤醒事件: task_report → TL 会话                                  │
│   TL 验收: approve → T1: review → done                            │
│                                                                  │
│ T2: blocked → ready (依赖 T1 完成)                                │
│ T2: ready → in_progress → review → done (同上流程)                 │
│                                                                  │
│ T3: blocked → ready → in_progress → review → done                 │
└──────────────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│ Phase 4: 团队综合                                                  │
│                                                                  │
│ 所有任务终态 → team_synthesis 唤醒事件                              │
│ TL 汇总各任务报告, 生成最终团队报告                                  │
│ 用户看到完整的"新产品上线方案"                                       │
└──────────────────────────────────────────────────────────────────┘
```

### 9.2 并行执行

无依赖的任务可以并行：

```
T1(无依赖) ──▶ Agent A 执行中 ──▶ done
T2(无依赖) ──▶ Agent B 执行中 ──▶ done    (并行)
T3(依赖T1,T2) ──▶ blocked ──▶ ready ──▶ Agent C 执行 ──▶ done
```

并行受 `member_concurrency` 约束（默认 1 = 同成员串行，不同成员可并行）。

---

## 10. 数据流总览

```
用户需求
    │
    ▼
TL 会话 (HarnessV2Engine)
    │
    ├──▶ TurnPlanner → TaskFrame(execution_target="team_member")
    │
    ├──▶ publish_team_planner_frames()
    │         │
    │         ▼
    │    TeamTask[] 创建
    │         │
    │         ├──▶ 有 assignee → enqueue_wake_event(task_assigned)
    │         │
    │         └──▶ 无 assignee → 进入竞标
    │                   │
    │                   ▼
    │              select_bid_candidates()
    │                   │
    │                   ▼
    │              bid_request 唤醒 × N
    │                   │
    │                   ▼
    │              bid_judge 唤醒 (TL 裁决)
    │                   │
    │                   ▼
    │              中标者 → task_assigned 唤醒
    │
    ▼
成员会话 (独立 HarnessV2Engine)
    │
    ├──▶ 成员 Agent 执行任务
    │
    ├──▶ 提交报告 + 黑板建议
    │
    ▼
TL 验收 (task_report 唤醒)
    │
    ├──▶ approve → done
    ├──▶ rework → task_rework 唤醒 → 成员重做
    └──▶ escalate → escalated
    │
    ▼
所有任务终态 → team_synthesis → 最终报告
```

---

## 11. 面试题

### Q1: 任务竞标系统是如何设计的？为什么需要竞标而不是直接分配？

**答：** 竞标系统模拟了真实团队中的"任务认领"场景。设计要点：

1. **候选筛选**：按 `expertise_tags` 与任务文本的重叠度计分，选 top-3
2. **多轮辩论**：1 轮陈述 + N 轮反驳（默认 3 轮），成员可以看到其他人的发言并反驳
3. **HP 淘汰**：每轮 TL 打分(0-10)，HP 扣减 `(10-score)×3`，归零淘汰
4. **最终裁决**：TL 从存活候选中选择中标者

竞标的价值：当 TL 不确定谁最适合时，通过竞争让 Agent 自己展示能力，比直接分配更灵活。直接分配适用于 TL 明确知道谁来做的情形。

### Q2: 黑板系统的去重策略是怎样的？

**答：** 黑板使用三级去重：

1. **批次内去重**：同一次写入中，规范化后相同的内容只保留一条
2. **精确去重**：与已有 active 条目规范化后完全相同 → 跳过
3. **子串去重**：
   - 新内容是既有条目的子串 → 跳过（已有更完整版本）
   - 既有条目是新内容的子串 → 合并更新既有条目（content/tags/citation 全部更新）

这种设计让黑板成为一个"活文档"：信息会不断被补充和完善，而不是简单堆积。

### Q3: 任务状态机中为什么 `escalated` 可以从几乎所有状态转换？

**答：** `escalated` 表示"升级给人处理"，是一个安全阀。在实际运行中，Agent 可能遇到无法处理的情况（如需要用户提供额外信息、遇到权限问题等），此时必须有一种机制将任务交给人工处理，而不是让 Agent 无限重试或静默失败。

因此，除了两个终态（`done`、`escalated` 自身），所有状态都可以流转到 `escalated`。这是一个重要的容错设计。

### Q4: 唤醒事件的租约和心跳机制是如何工作的？

**答：** 唤醒事件使用**租约 + 心跳**保证可靠执行：

1. **认领**：`claim_wake_event()` 原子地将 `pending` 更新为 `claimed`（乐观锁）
2. **心跳**：后台线程每 30 秒更新 `updated_at`，证明执行者还活着
3. **超时回收**：`recover_orphaned_wake_events()` 扫描 `claimed` 且 `updated_at` 超过 180 秒的事件，恢复为 `pending`
4. **重新派发**：`dispatch_pending_wake_events()` 定期扫描 `pending` 事件并启动后台线程执行

这确保了即使执行线程崩溃，任务也不会丢失。

### Q5: 成员执行串行控制是如何实现的？

**答：** 双重额度判定，覆盖并发和重启场景：

1. **进程内计数**（`_member_slot_counts`）：用字典 + 线程锁记录每个成员当前正在执行的任务数。覆盖 DB 写入前的并发窗口
2. **DB 计数**（`_member_in_progress_count()`）：查询 `status="in_progress"` 的任务数。覆盖进程重启后的存量执行

当额度已满时，唤醒事件保持 `pending`，记录 `wake_queued` 审计。当前任务终态后，`_drain_member_queue()` 自动出队最老的排队事件并启动执行。

### Q6: TL 如何感知团队状态并做出决策？

**答：** TL 的每次会话都会注入结构化上下文：

1. **花名册**（`team_roster_lines()`）：所有成员的 agent_id、名称、角色、能力标签
2. **未闭环任务**（`open_tasks_summary()`）：当前 blocked/pending/in_progress/review/rework 状态的任务
3. **黑板**（`blackboard_context_lines()`）：按当前消息关键词匹配的 top-K 条目
4. **Planner 上下文**（`TeamPlannerContext`）：成员列表 + 能力描述，供 TurnPlanner 生成 `execution_target="team_member"` 的 TaskFrame

这些信息让 TL 在分解任务时能做出合理的分配决策。

### Q7: 如果 TL 验收时发现成员的报告质量不好，系统如何处理？

**答：** TL 可以输出 `verdict: "rework"`，触发以下流程：

1. 任务状态：`review → rework`
2. 创建 `task_rework` 唤醒事件
3. 成员收到退回意见（`build_member_task_message(rework=True)`），包含 TL 的退回意见
4. 成员在自己的会话中重做任务
5. 完成后再次提交报告 → `rework → in_progress → review`
6. TL 再次验收

如果 TL 认为任务超出 Agent 能力，可以输出 `verdict: "escalate"`，任务进入 `escalated` 终态，等待人工处理。

### Q8: 团队协作与 Harness v2 引擎是如何集成的？

**答：** 团队协作构建在 Harness v2 之上，而非替代它：

1. **TL 会话**：`interaction_mode="team_tl"`，使用独立的 `ChatSession`（关联 `team_id`），TL 在自己的 Harness v2 中运行 TurnPlanner，生成 `execution_target="team_member"` 的 TaskFrame
2. **帧分发**：`publish_team_planner_frames()` 将 team_member 帧转换为 `TeamTask`，从 Harness 执行队列中移除
3. **成员会话**：每个成员有自己的 `ChatSession`（关联 `team_id` + `agent_id`），唤醒事件触发独立的 `HarnessV2Engine.run()` 执行
4. **结果回传**：成员执行完成后，报告存入 `TeamTask.report_json`，TL 通过唤醒事件验收

这种设计让团队协作和单聊共享同一套执行引擎，只是增加了任务调度和协作协议层。
