# AWS Bedrock Converse API Tool Use Demos

这个目录包含了使用 boto3 Converse API 进行 tool use 的演示代码。

## 文件说明

### 1. `demo_boto3_tool_use.py` - 完整功能演示
最全面的 demo，测试多个场景：
- 单个工具调用
- 多个工具调用（同时查询多个城市天气）
- 带参数的工具调用
- 无需工具的对话

**测试模型:**
- `moonshot.kimi-k2-thinking`
- `minimax.minimax-m2`

**运行:**
```bash
python demo_boto3_tool_use.py
```

### 2. `demo_simple_test.py` - 简化演示
简洁版本，清晰展示每个 content block 的结构：
- Text blocks (文本输出)
- Reasoning content blocks (推理过程)
- Tool use blocks (工具调用)

**运行:**
```bash
python demo_simple_test.py
```

### 3. `demo_reasoning_test.py` - 复杂推理测试
测试需要多步推理的复杂任务：
- 查询多个城市天气
- 计算平均温度
- 分析并推荐最佳城市

**运行:**
```bash
python demo_reasoning_test.py
```

## 核心功能展示

### 1. Tool Definition (工具定义)

使用 `toolSpec` 格式定义工具：

```python
WEATHER_TOOL = {
    "toolSpec": {
        "name": "get_weather",
        "description": "Get current weather information for a city.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "The city name"
                    },
                    "units": {
                        "type": "string",
                        "enum": ["celsius", "fahrenheit"]
                    }
                },
                "required": ["city"]
            }
        }
    }
}
```

### 2. Multi-turn Conversation (多轮对话)

自动处理工具调用循环：
```
User Query → Model Response (tool_use) → Execute Tools → Send Results → Final Response (end_turn)
```

### 3. Content Block Types (内容块类型)

每个响应可能包含多种类型的 content blocks：

#### reasoningContent (推理内容)
```python
{
    "reasoningContent": {
        "reasoningText": {
            "text": "模型的思考过程..."
        }
    }
}
```

#### text (文本输出)
```python
{
    "text": "回答内容..."
}
```

#### toolUse (工具调用)
```python
{
    "toolUse": {
        "toolUseId": "toolu_123",
        "name": "get_weather",
        "input": {"city": "Beijing"}
    }
}
```

### 4. Reasoning Content 显示

默认情况下，推理内容不会显示。需要通过 `additionalModelRequestFields` 启用：

```python
response = bedrock.converse(
    modelId=model_id,
    messages=messages,
    toolConfig=tool_config,
    additionalModelRequestFields={
        "include_reasoning": True  # 启用推理内容输出
    }
)
```

**注意:** 不同模型对 reasoning content 的支持不同：
- `moonshot.kimi-k2-thinking` - 简短推理过程
- `minimax.minimax-m2` - 详细推理过程

## 模型对比

### moonshot.kimi-k2-thinking
- ✅ 支持工具调用
- ✅ 输出推理内容（简洁）
- ✅ 多轮对话
- 特点：在工具调用前会输出内部标记

### minimax.minimax-m2
- ✅ 支持工具调用
- ✅ 输出推理内容（详细）
- ✅ 多轮对话
- 特点：推理过程非常详细，展示完整思考链

## 输出示例

### Iteration 1 - Tool Use
```
Stop Reason: tool_use

[🧠 Reasoning Content Block 0]:
The user wants me to check the weather in Beijing...

[💬 Text Block 1]:
I'll check the weather for you.

[🔧 Tool Use Block 2]:
  Tool: get_weather
  Input: {"city": "Beijing"}
```

### Iteration 2 - Final Response
```
Stop Reason: end_turn

[🧠 Reasoning Content Block 0]:
The weather data shows 15°C with partly cloudy conditions...

[💬 Text Block 1]:
The current weather in Beijing is 15°C, partly cloudy with 45% humidity.
```

## 环境要求

```bash
pip install boto3
```

配置 AWS 凭证：
```bash
aws configure
# 或设置环境变量
export AWS_ACCESS_KEY_ID=your_key
export AWS_SECRET_ACCESS_KEY=your_secret
export AWS_REGION=us-east-1
```

## 关键学习点

1. **Content Block 顺序很重要**:
   - reasoningContent 通常在最前面
   - 然后是 text 或 toolUse
   - 一个响应可能包含多个不同类型的 blocks

2. **字段名称注意**:
   - 是 `reasoningContent` 不是 `reasoning`
   - 内部结构是 `reasoningText.text`

3. **工具执行循环**:
   - Model 返回 `tool_use` → 执行工具 → 发送结果 → Model 返回 `end_turn`
   - 需要手动实现这个循环

4. **Token Usage**:
   - 每次 converse 调用都会返回 token 使用统计
   - 包括 inputTokens, outputTokens, totalTokens

## 参考资源

- [AWS Bedrock Converse API 文档](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html)
- [Tool Use 最佳实践](https://docs.aws.amazon.com/bedrock/latest/userguide/tool-use.html)
