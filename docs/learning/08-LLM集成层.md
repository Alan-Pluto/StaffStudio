# 08 - LLM 集成层

## 1. 概述

StaffStudio 的 LLM 集成层是一个**多协议、可观测、带结构化提示管理**的大模型抽象层，核心文件为 `backend/app/llm/client.py`（约 1900 行）。它将不同厂商的 API 差异封装在统一的接口之下，让上层业务代码无需关心底层模型是 GPT、Claude 还是 Gemini。

### 设计目标

| 目标 | 实现方式 |
|------|---------|
| 多协议统一 | 4 种 API 协议通过 ProtocolDriver 接口统一 |
| 结构化提示 | Stage Protocol 标准化所有 LLM 调用的输入输出 |
| JSON 可靠性 | 最多 3 次自动修复 + JSON Sequence 支持 |
| 可观测性 | 每次调用记录完整的请求/响应/耗时指标 |
| 安全 | API Key 加密存储、密钥字段自动脱敏 |
| 容错 | 空响应重试、图片参数降级、协议降级 |

### 关键文件

```
backend/app/llm/
├── client.py               # 核心客户端（~1900 行）
├── model_protocols.py      # 协议枚举与验证
├── model_config_resolver.py # 模型配置解析与安全校验
├── protocol_drivers.py     # 4 种协议的驱动实现（~900 行）
├── stage_protocol.py       # Stage 协议：结构化提示管理
├── output_policy.py        # 按操作类型的输出策略
├── schemas.py              # Pydantic 数据模型
└── prompts/                # 27 个提示词模板
    ├── unified_agent_prompt.md
    ├── turn_planner_prompt.md
    ├── harness_agent_prompt.md
    ├── step_agent_prompt.md
    ├── response_generator_prompt.md
    ├── memory_extractor_prompt.md
    ├── knowledge_search_prompt.md
    ├── knowledge_bucket_prompt.md
    ├── knowledge_discovery_prompt.md
    ├── knowledge_document_route_prompt.md
    ├── reflection_prompt.md
    ├── router_prompt.md
    └── ... (共 27 个)
```

---

## 2. 协议支持

### 2.1 四种 API 协议

```python
class ModelApiProtocol(StrEnum):
    OPENAI_CHAT_COMPLETIONS = "openai_chat_completions"  # OpenAI 兼容接口
    OPENAI_RESPONSES = "openai_responses"                 # OpenAI Responses API
    ANTHROPIC_MESSAGES = "anthropic_messages"             # Anthropic Messages API
    GEMINI_GENERATE_CONTENT = "gemini_generate_content"   # Google Gemini
```

### 2.2 ProtocolDriver 接口

所有协议驱动都实现统一的 `ProtocolDriver` 接口：

```python
class ProtocolDriver(Protocol):
    request_kind: str                                    # 请求类型标识

    def observable_request(self, request, *, stream) -> dict:  # 可观测的请求格式
    def complete(self, request) -> Any:                  # 同步完成
    def stream(self, request) -> Iterator[Any]:          # 流式输出
```

### 2.3 四种驱动实现

| 驱动类 | 协议 | SDK/方式 | 请求类型标识 |
|--------|------|---------|-------------|
| `ChatCompletionsDriver` | OpenAI Chat | `openai` SDK | `chat.completions` |
| `OpenAIResponsesDriver` | OpenAI Responses | `openai` SDK | `responses` |
| `AnthropicMessagesDriver` | Anthropic | `anthropic` SDK | `anthropic.messages` |
| `GeminiGenerateContentDriver` | Gemini | `httpx` 直接调用 | `gemini.generate_content` |

### 2.4 协议归一化

不同协议的请求和响应格式各不相同，每个驱动负责归一化：

**请求归一化**：
- ChatCompletions：标准 `messages` 数组
- Responses：转换为 `input` 格式
- Anthropic：分离 `system` 和 `messages`，处理 `base_url` 路径
- Gemini：转换为 `contents` + `systemInstruction` 格式

**响应归一化**：
所有驱动的响应都归一化为统一结构：
```python
SimpleNamespace(
    id="...",
    choices=[SimpleNamespace(
        message=SimpleNamespace(content="..."),
        finish_reason="stop",
    )],
    usage=SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
    ),
)
```

### 2.5 客户端初始化

```python
class LLMClient:
    def __init__(self, model_config: ModelConfig):
        protocol = ModelApiProtocol(model_config.api_protocol)
        api_key = decrypt_secret(model_config.api_key_encrypted)

        if protocol is ModelApiProtocol.OPENAI_CHAT_COMPLETIONS:
            self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
            self.driver = ChatCompletionsDriver(self.client)
        elif protocol is ModelApiProtocol.OPENAI_RESPONSES:
            self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
            self.driver = OpenAIResponsesDriver(self.client)
        elif protocol is ModelApiProtocol.ANTHROPIC_MESSAGES:
            # Anthropic SDK 的 base_url 处理有特殊逻辑
            self.client = Anthropic(api_key=api_key, timeout=timeout, max_retries=0)
            self.driver = AnthropicMessagesDriver(self.client)
        elif protocol is ModelApiProtocol.GEMINI_GENERATE_CONTENT:
            self.client = httpx.Client(timeout=timeout)
            self.driver = GeminiGenerateContentDriver(self.client, base_url, api_key, model)
```

---

## 3. 核心方法

### 3.1 `generate_text()` — 单文本补全

```python
def generate_text(
    self,
    system_prompt: str,
    user_payload: dict[str, Any] | str,
    response_format: dict[str, str] | None = None,
    cancellation: CancellationToken | None = None,
) -> str:
```

核心流程：
1. 准备用户输入（`_prepare_user_input`）：提取上下文消息 + 序列化当前输入
2. 构建请求消息（`_request_messages`）：system + context + user
3. 适配请求（`_fit_request_messages`）：确保格式兼容
4. 空响应重试：最多 `EMPTY_RESPONSE_RETRIES = 2` 次
5. 通过 ProtocolDriver 发送请求
6. 记录可观测性 span（耗时、token 用量、响应内容等）
7. 记录 Stage 交换历史

关键常量：
```python
EMPTY_RESPONSE_RETRIES = 2
DEFAULT_MODEL_API_TIMEOUT_SECONDS = 600.0
DEFAULT_INPUT_TOKEN_BUDGET = 32_000
```

### 3.2 `generate_text_stream()` — 流式补全

```python
def generate_text_stream(
    self,
    system_prompt: str,
    user_payload: dict[str, Any] | str,
    cancellation: CancellationToken | None = None,
) -> Iterator[str]:
```

流式处理的特殊逻辑：
1. **缓冲首个非空文本**：跳过前导空白 chunk，确保首帧有意义
2. **Reasoning 文本追踪**：单独记录推理内容（如 DeepSeek 的 `reasoning_content`）
3. **Tool Call 增量收集**：收集 `tool_calls` 的 delta 增量
4. **取消支持**：每个 chunk 都检查 `CancellationToken`
5. **图片降级**：如果模型不支持图片参数，自动移除后重试

可观测性指标：
```python
span.finish(
    ttft_ms=first_content_ms,           # Time To First Token
    stream_duration_ms=...,             # 流式传输持续时间
    output_chars=output_chars,          # 输出字符数
    stream_chunks=chunk_count,          # chunk 总数
    reasoning_chars=reasoning_chars,    # 推理内容字符数
    finish_reasons=sorted(finish_reasons),
)
```

### 3.3 `generate_json()` — JSON 模式

```python
def generate_json(
    self,
    system_prompt: str,
    user_payload: dict[str, Any],
    cancellation: CancellationToken | None = None,
    *,
    accept_json_sequence: bool = False,
) -> Any:
```

JSON 模式是最复杂的方法，包含多层容错：

```
第 1 次尝试
  ├── Anthropic → 在 system_prompt 末尾追加 JSON 指令
  ├── 其他协议 → 使用 response_format={"type": "json_object"}
  │     └── 如果不支持 → 降级到普通 generate_text
  └── 解析结果
        ├── 成功 → 返回 parsed JSON
        ├── JSON Sequence 模式 → 尝试解析连续 JSON 值
        └── 失败 → 进入修复循环

修复循环（最多 JSON_REPAIR_ATTEMPTS = 3 次）
  ├── 将上次的错误输出和解析错误注入下一轮输入
  ├── 指令："上一轮输出不是合法 JSON。请基于原始任务上下文重新输出完整、可解析的 JSON object。"
  └── 重新调用 → 解析 → 成功/继续修复

最终失败 → 抛出 LLMError，附带所有尝试的预览
```

关键设计：
```python
JSON_REPAIR_ATTEMPTS = 3

# 修复上下文注入
next_payload["_json_repair"] = {
    "attempt": attempt + 1,
    "max_attempts": JSON_REPAIR_ATTEMPTS,
    "previous_output": _preview(text),      # 上次输出预览
    "parser_error": str(exc),               # 解析错误信息
    "instruction": "上一轮输出不是合法 JSON...",
}
```

### 3.4 `generate_json_sequence()` — 序列感知 JSON

```python
def generate_json_sequence(
    self,
    system_prompt: str,
    user_payload: dict[str, Any],
    cancellation: CancellationToken | None = None,
) -> Any:
```

接受连续的多个顶层 JSON 值（JSON Lines 风格）。某些 Responses 兼容的 provider 会将多个 action 展平为连续 JSON 对象，此方法保留它们的原始顺序。

---

## 4. Stage Protocol（阶段协议）

文件：`backend/app/llm/stage_protocol.py`（~242 行）

### 4.1 设计理念

StaffStudio 的 Agent 系统有多个阶段（Router → TurnPlanner → StepAgent → Reflection → ResponseGenerator），每个阶段都需要不同的提示词和输出格式。Stage Protocol 提供了一套**标准化的输入输出契约**。

### 4.2 核心数据结构

```python
STAGE_PROTOCOL_KEY = "_agent_stage"

def stage_payload(
    *,
    phase: str,                    # 阶段名称：Router / TurnPlanner / StepAgent ...
    user_message: str,             # 用户原始消息
    conversation_context: dict,    # 对话上下文
    memory_context: list | str,    # 记忆上下文
    instructions: str,             # 阶段特定指令
    stage_data: dict,              # 阶段独有数据
    output_contract: dict | str,   # 输出格式约束
) -> dict[str, Any]:
    return {
        "_agent_stage": {
            "phase": phase,
            "instructions": instructions,
            "output_contract": output_contract,
            "memory": memory_text,
            "turn_time": turn_time,
        },
        "user_message": user_message,
        "conversation_context": conversation_context,
        **stage_data,
    }
```

### 4.3 用户消息渲染

```python
def render_stage_user_message(user_payload, *, include_turn_header=True) -> str:
```

将 Stage 数据渲染为结构化的用户消息，包含以下段落：

```
用户记忆：
- 用户偏好简洁回复
- 上次讨论了项目排期

本轮时间：
2024-01-15 14:30:00

本轮用户输入：
帮我查一下员工手册中关于年假的规定

当前阶段：
Router

思考要求：
保留完成当前阶段所需的简短思考；不要复述上下文...

阶段规则：
你是一个任务路由器，负责判断用户意图...

当前阶段独有内容：
{"active_tasks":[],"available_skills":[...]}

输出约束：
{"decision":"continue_active | switch_to_pending | ...", ...}
```

### 4.4 输出 Schema 定义

每个阶段都有严格的输出 Schema：

**Router 输出 Schema**：
```python
ROUTER_OUTPUT_SCHEMA = {
    "decision": "continue_active | switch_to_pending | create_pending | ...",
    "selected_task_id": "string?",
    "target_skill_id": "string?",
    "confidence": "number",
    "user_intent": "string?",
    "task_frames": [...],
    "pending_tasks": [...],
    "task_updates": [...],
}
```

**Turn Planner 输出 Schema**：
```python
TURN_PLANNER_OUTPUT_SCHEMA = {
    "decision": "continue_active | switch_to_pending | complete_task | ...",
    "task_frames": [{
        "kind": "sop | conversation",
        "execution_target": "self | team_member",
        "assignee_agent_id": "...",
        "activation_condition": {},
        ...
    }],
}
```

**Step Agent 输出 Schema**：
```python
STEP_AGENT_OUTPUT_SCHEMA = {
    "action": "ask_user | clarify | reply | advance | call_tool | query_knowledge | handoff",
    "reply": "string?",
    "slot_updates": "object",
    "tool_call": {"name": "string", "arguments": "object"},
    "knowledge_query": {
        "query": "string",
        "query_type": "answer | policy_check | tool_discovery | skill_discovery?",
    },
}
```

**Reflection 输出 Schema**：
```python
REFLECTION_OUTPUT_SCHEMA = {
    "action": "pass | retry_tool | try_other_tool | ask_user | revise_step | stop",
    "needs_retry": "boolean",
    "target_tool_name": "string?",
}
```

---

## 5. 提示词管理

### 5.1 提示词目录

所有提示词以 Markdown 文件形式存放在 `backend/app/llm/prompts/` 目录下，共 27 个：

| 提示词文件 | 用途 |
|-----------|------|
| `unified_agent_prompt.md` | 统一 Agent 系统提示词 |
| `turn_planner_prompt.md` | 轮次规划器 |
| `harness_agent_prompt.md` | Harness 引擎 Agent |
| `step_agent_prompt.md` | 步骤执行器基础提示词 |
| `step_agent_tool_rules.md` | 工具调用规则 |
| `step_agent_tool_continuation_rules.md` | 工具续调规则 |
| `step_agent_knowledge_rules.md` | 知识查询规则 |
| `step_agent_awaiting_input_rules.md` | 等待用户输入规则 |
| `step_agent_general_skill_rules.md` | 通用技能规则 |
| `step_agent_repair_rules.md` | 修复规则 |
| `response_generator_prompt.md` | 回复生成器 |
| `reflection_prompt.md` | 反思/自检 |
| `router_prompt.md` | 路由器 |
| `memory_extractor_prompt.md` | 记忆提取器 |
| `knowledge_search_prompt.md` | 知识搜索（Bucket 路由） |
| `knowledge_bucket_prompt.md` | 知识 Bucket 规划 |
| `knowledge_discovery_prompt.md` | 知识发现 |
| `knowledge_document_route_prompt.md` | 文档路由 |
| `skill_distiller_prompt.md` | 技能蒸馏 |
| `skill_editor_prompt.md` | 技能编辑 |
| `skill_reflection_prompt.md` | 技能反思 |
| `general_skill_read_prompt.md` | 通用技能读取 |
| `general_skill_repair_prompt.md` | 通用技能修复 |
| `general_skill_reply_prompt.md` | 通用技能回复 |
| `general_skill_review_prompt.md` | 通用技能审核 |
| `general_skill_runner_prompt.md` | 通用技能运行 |
| `general_skill_selector_prompt.md` | 通用技能选择 |

### 5.2 提示词加载

```python
PROMPT_DIR = paths.resource_dir() / "app" / "llm" / "prompts"
BUCKET_PROMPT = PROMPT_DIR / "knowledge_bucket_prompt.md"
DISCOVERY_PROMPT = PROMPT_DIR / "knowledge_discovery_prompt.md"
SEARCH_PROMPT = PROMPT_DIR / "knowledge_search_prompt.md"
DOCUMENT_ROUTE_PROMPT = PROMPT_DIR / "knowledge_document_route_prompt.md"
```

提示词通过 `Path.read_text(encoding="utf-8")` 直接加载，不经过模板引擎——保持简单和可预测。

---

## 6. 模型配置管理

### 6.1 配置解析器

文件：`backend/app/llm/model_config_resolver.py`（~186 行）

```python
@dataclass(frozen=True)
class ResolvedModelConfig:
    id: str
    tenant_id: str
    api_protocol: ModelApiProtocol
    base_url: str | None
    api_key_encrypted: str
    model: str
    temperature: float
    max_output_tokens: int
    protocol_options: Mapping[str, Any]    # 冻结的协议选项
    legacy_extra_body: Mapping[str, Any]   # 冻结的额外请求体
    config_revision: int
    security_revision: int
    purpose: Literal["runtime", "verification"]
    timeout_seconds: float | None = None
```

### 6.2 信任与验证机制

模型配置有一套**信任状态管理**：

```python
def resolve_model_config_for_runtime(db, tenant_id, config_id) -> ResolvedModelConfig:
    row = _current_model_config(db, tenant_id, config_id)

    if not row.enabled:
        raise HTTPException(409, "MODEL_CONFIG_DISABLED")

    # 旧版 trusted 模型必须是 OpenAI Chat 协议
    if row.trust_status == "legacy_trusted":
        if protocol is not ModelApiProtocol.OPENAI_CHAT_COMPLETIONS:
            raise HTTPException(409, "MODEL_CONFIG_VERIFICATION_REQUIRED")

    # 新版模型必须通过验证，且指纹匹配
    elif row.trust_status != "verified" or row.verified_fingerprint != _fingerprint(row):
        raise HTTPException(409, "MODEL_CONFIG_VERIFICATION_REQUIRED")

    return _snapshot(row, protocol, purpose="runtime")
```

### 6.3 配置指纹

当模型配置的关键字段发生变化时，需要重新验证：

```python
def model_config_fingerprint(*, api_protocol, base_url, model, key_revision, protocol_options, security_revision):
    payload = {
        "fingerprint_version": 1,
        "api_protocol": api_protocol,
        "base_url": _normalize_base_url(base_url),
        "model": model,
        "key_revision": key_revision,
        "protocol_options": protocol_options,
        "security_revision": security_revision,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
```

### 6.4 输出策略

文件：`backend/app/llm/output_policy.py`

```python
# 控制面路由器不需要重试空响应（有确定性降级逻辑）
OPERATION_EMPTY_RESPONSE_RETRIES = {
    "knowledge.document_route": 0,
    "knowledge.bucket_route": 0,
}

def operation_output_tokens(operation: str, configured_tokens: int) -> int:
    # 所有阶段统一使用模型配置的 Max Tokens
    return max(1, int(configured_tokens or 1))
```

---

## 7. 可观测性

### 7.1 Span 记录

每次 LLM 调用都会创建详细的可观测性 span：

```python
span = start_llm_call(
    model=self.model,
    model_name=self.model_config_name,
    endpoint=_endpoint_label(self.base_url),
    request_kind=driver.request_kind,     # "chat.completions" / "anthropic.messages" ...
    stream=False,
    attempt=attempt + 1,
    retry_count=attempt,
    max_attempts=empty_response_retries + 1,
    max_output_tokens=current_max_tokens,
    thinking_mode=self.thinking_mode or "provider_default",
    request_messages=_observable_messages(request_messages),
    request_parameters=_observable_request_parameters(...),
    request_payload=_observable_value(...),
    # 请求形状指标
    system_prompt_chars=len(system_prompt),
    context_message_count=len(context_messages),
    context_text_chars=context_chars,
    request_text_chars=request_chars,
    request_message_count=len(request_messages),
    request_message_roles=["system", "user"],
    request_message_chars=[1200, 3500],
    request_prefix_fingerprints=["a1b2c3d4e5f6g7h8", ...],
)
```

### 7.2 安全脱敏

```python
_OBSERVABILITY_SECRET_KEYS = {"authorization", "api-key", "x-api-key", ...}

def _observable_model_value(value, *, _seen=None, _depth=0):
    # 递归转换 SDK 对象为 JSON 兼容格式
    # 自动脱敏敏感字段
    if str(key).lower() in _OBSERVABILITY_SECRET_KEYS:
        return "[REDACTED]"
    # 防止循环引用
    if identity in seen:
        return "[circular reference]"
    # 限制递归深度
    if _depth > 20:
        return "[maximum audit depth reached]"
```

### 7.3 请求前缀指纹

```python
def _request_prefix_fingerprints(messages):
    # 为每条消息计算累积 SHA256 指纹（前 16 位）
    # 用于快速识别请求结构变化
```

---

## 8. 容错机制

### 8.1 空响应重试

```python
EMPTY_RESPONSE_RETRIES = 2

for attempt in range(empty_response_retries + 1):
    completion = driver.complete(request)
    content = _completion_message_content(completion)
    if content.strip():
        return content  # 成功
    # 记录诊断信息
    empty_diagnostics.append(_completion_empty_diagnostic(completion, attempt + 1))
# 所有重试都失败
raise LLMError(_empty_response_detail(self, empty_diagnostics))
```

### 8.2 图片参数降级

如果模型不支持图片输入：
```python
if _image_parameter_unsupported(exc) and _messages_have_images(request_messages):
    request_messages = _without_image_parts(request_messages)
    continue  # 重试不带图片的请求
```

### 8.3 JSON Mode 降级

```python
if json_mode_supported and _response_format_unsupported(text):
    json_mode_supported = False
    text = self.generate_text(system_prompt, next_payload)
```

### 8.4 取消令牌

```python
class CancellationToken:
    def __init__(self):
        self._event = Event()

    def cancel(self):
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()
```

每个 ProtocolDriver 在发送请求和接收每个 stream chunk 时都会检查取消状态。

### 8.5 图片上传限制

```python
_MAX_IMAGE_BYTES = 5 * 1024 * 1024       # 单张图片最大 5MB
_MAX_IMAGE_COUNT = 6                       # 最多 6 张图片
_MAX_TOTAL_IMAGE_BYTES = 18 * 1024 * 1024  # 总图片大小上限 18MB
_MAX_REQUEST_BYTES = 25 * 1024 * 1024      # 请求体最大 25MB
```

---

## 9. Thinking Mode 支持

系统支持模型的"思考模式"（如 DeepSeek 的推理模式）：

```python
self.thinking_mode = (
    _thinking_mode_from_extra_body(self.extra_body)
    or _thinking_mode_for_model(
        settings.model_thinking_mode,
        settings.model_thinking_models,
        self.model,
    )
)
```

思考模式通过 `extra_body` 传递给模型：
```python
def _thinking_request_kwargs(mode, extra_body):
    if normalized:
        body["thinking"] = {**thinking, "type": normalized}
    return {"extra_body": body} if body else {}
```

流式模式下，推理内容（`reasoning_content`）会被单独追踪和记录。

---

## 10. 架构总结

```
┌──────────────────────────────────────────────────────┐
│                    上层业务代码                         │
│  Harness / Knowledge / Skills / Router / Planner     │
├──────────────────────────────────────────────────────┤
│                  LLMClient 接口层                     │
│  generate_text() / generate_text_stream()            │
│  generate_json() / generate_json_sequence()          │
├──────────────────────────────────────────────────────┤
│              Stage Protocol 结构化提示                 │
│  stage_payload() / render_stage_user_message()       │
├──────────────────────────────────────────────────────┤
│              ProtocolDriver 协议驱动                   │
│  ChatCompletions / Responses / Anthropic / Gemini    │
├──────────────────────────────────────────────────────┤
│              Model Config Resolver                    │
│  信任验证 / 指纹管理 / 配置解析                         │
├──────────────────────────────────────────────────────┤
│              Observability 可观测性                    │
│  Span 记录 / 安全脱敏 / 请求指纹                       │
└──────────────────────────────────────────────────────┘
```

---

## 11. 面试高频问题

### Q1：为什么要自己封装 LLM 客户端而不是直接用 LangChain？

**答**：三个核心原因：
1. **精确控制**：我们需要精确控制每次请求的格式、重试策略和错误处理。LangChain 的抽象层太厚，出了问题很难定位。
2. **多协议统一**：我们的系统需要同时支持 OpenAI、Anthropic、Gemini 三种完全不同的 API 协议，LangChain 对 Responses API 和 Gemini 的支持不够灵活。
3. **可观测性**：我们需要记录每次 LLM 调用的完整请求/响应/耗时/token 用量，包括流式场景下的 TTFT（Time To First Token）。自研的 ProtocolDriver 接口让可观测性贯穿所有协议。

### Q2：JSON 修复机制是怎么设计的？

**答**：JSON 修复是一个三层容错设计：
1. **第一层**：使用 `response_format={"type": "json_object"}` 强制 JSON 输出（Anthropic 则在 prompt 中追加 JSON 指令）
2. **第二层**：如果解析失败，将错误信息和上次输出注入下一轮请求，让模型自我修正（最多 3 次）
3. **第三层**：如果仍然失败，尝试 JSON Sequence 解析（适用于某些 provider 输出多个 JSON 值的情况）

关键设计决策是**修复上下文注入**——不是简单重试，而是告诉模型"上次输出了什么、哪里解析失败了"，让修复有针对性。

### Q3：Stage Protocol 解决了什么问题？

**答**：Agent 系统有多个处理阶段（Router → Planner → StepAgent → Reflection → ResponseGenerator），每个阶段需要：
- 不同的系统提示词
- 不同的输入数据结构
- 不同的输出格式约束

Stage Protocol 将这三者标准化为一个统一的 `stage_payload()`，确保：
1. 每个阶段的输入格式一致，LLM 能稳定理解
2. 输出约束（`output_contract`）明确，减少格式错误
3. 记忆、时间等上下文信息自动注入
4. 可追溯——每次调用都有完整的阶段记录

### Q4：如何处理不同模型的 Thinking Mode？

**答**：Thinking Mode（如 DeepSeek 的推理模式）通过 `extra_body` 传递：
1. 从模型配置的 `extra_body` 或全局设置中检测 thinking mode
2. 只有配置了 `model_thinking_mode` 且在 `model_thinking_models` 白名单中的模型才启用
3. 流式模式下，推理内容（`reasoning_content`）会被单独追踪，不计入输出字符数
4. 可观测性 span 中记录 `reasoning_chars`，方便分析推理开销

### Q5：模型配置的安全机制是怎样的？

**答**：三层安全机制：
1. **加密存储**：API Key 使用 `encrypt_secret()` 加密后存入数据库，运行时通过 `decrypt_secret()` 解密
2. **信任验证**：新配置的模型必须通过验证（`verification_attempt`），验证通过后记录指纹
3. **变更检测**：当协议、URL、模型名、Key 等关键字段变化时，指纹改变，需要重新验证
4. **审计脱敏**：可观测性数据中的敏感字段（api-key、authorization 等）自动替换为 `[REDACTED]`
