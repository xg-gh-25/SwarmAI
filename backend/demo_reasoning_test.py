#!/usr/bin/env python3
"""
Test reasoning content output from thinking models
"""

import json
import boto3


WEATHER_TOOL = {
    "toolSpec": {
        "name": "get_weather",
        "description": "Get current weather information for a city.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "The city name"},
                    "units": {
                        "type": "string",
                        "enum": ["celsius", "fahrenheit"],
                        "description": "Temperature units",
                    }
                },
                "required": ["city"],
            }
        }
    }
}

CALCULATOR_TOOL = {
    "toolSpec": {
        "name": "calculate",
        "description": "Perform mathematical calculations.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "Math expression to evaluate"},
                },
                "required": ["expression"],
            }
        }
    }
}


def get_weather(city: str, units: str = "celsius"):
    """Mock weather function"""
    mock_data = {
        "beijing": {"temp": 15, "condition": "Partly Cloudy", "humidity": 45},
        "new york": {"temp": 22, "condition": "Sunny", "humidity": 60},
        "london": {"temp": 12, "condition": "Rainy", "humidity": 80},
    }
    city_lower = city.lower()
    if city_lower in mock_data:
        weather = mock_data[city_lower]
        temp = weather["temp"] if units == "celsius" else (weather["temp"] * 9/5) + 32
        return {"city": city, "temperature": temp, "units": units, "condition": weather["condition"], "humidity": weather["humidity"]}
    return {"error": f"Weather data not available for {city}"}


def calculate(expression: str):
    """Simple calculator"""
    try:
        result = eval(expression)
        return {"expression": expression, "result": result}
    except Exception as e:
        return {"error": str(e)}


def test_complex_reasoning(model_id: str):
    """Test with a query that requires reasoning"""
    bedrock = boto3.client('bedrock-runtime', region_name='us-east-1')

    # Complex query that requires multi-step reasoning
    query = """I need to plan a trip. Check the weather in Beijing, New York, and London.
    Then calculate the average temperature across these three cities in celsius.
    Finally, tell me which city has the best weather for outdoor activities."""

    messages = [{"role": "user", "content": [{"text": query}]}]
    tool_config = {"tools": [WEATHER_TOOL, CALCULATOR_TOOL]}

    print(f"\n{'='*70}")
    print(f"Model: {model_id}")
    print(f"{'='*70}")
    print(f"Query: {query}")
    print(f"{'='*70}\n")

    for iteration in range(10):  # Allow more iterations for complex task
        print(f"--- Iteration {iteration + 1} ---")

        # Try with additional inference config to enable reasoning
        response = bedrock.converse(
            modelId=model_id,
            messages=messages,
            toolConfig=tool_config,
            # Enable reasoning output for thinking models
            additionalModelRequestFields={
                "include_reasoning": True
            } if "kimi" in model_id or "thinking" in model_id else {}
        )

        stop_reason = response['stopReason']
        output_message = response['output']['message']

        print(f"Stop Reason: {stop_reason}\n")

        # Print all content blocks with detailed type info
        for i, content_block in enumerate(output_message['content']):
            print(f"Content Block {i} - Keys: {list(content_block.keys())}")

            # Handle reasoning content (note: key is 'reasoningContent' not 'reasoning')
            if 'reasoningContent' in content_block:
                print(f"[🧠 Reasoning Content Block {i}]:")
                reasoning_content = content_block['reasoningContent']
                print(json.dumps(reasoning_content, indent=2, ensure_ascii=False))
                print()

            if 'reasoning' in content_block:
                print(f"[🧠 Reasoning Block {i}]:")
                print(content_block['reasoning'])
                print()

            if 'text' in content_block:
                print(f"[💬 Text Block {i}]:")
                print(content_block['text'])
                print()

            if 'toolUse' in content_block:
                tool_use = content_block['toolUse']
                print(f"[🔧 Tool Use Block {i}]:")
                print(f"  Tool: {tool_use['name']}")
                print(f"  Input: {json.dumps(tool_use['input'], indent=2)}")
                print()

        messages.append(output_message)

        if stop_reason == 'tool_use':
            # Execute tools and send results back
            tool_results = []
            for content_block in output_message['content']:
                if 'toolUse' in content_block:
                    tool_use = content_block['toolUse']
                    tool_name = tool_use['name']

                    if tool_name == 'get_weather':
                        result = get_weather(**tool_use['input'])
                    elif tool_name == 'calculate':
                        result = calculate(**tool_use['input'])
                    else:
                        result = {"error": f"Unknown tool: {tool_name}"}

                    print(f"[✅ Tool Result]: {json.dumps(result)}\n")

                    tool_results.append({
                        "toolUseId": tool_use['toolUseId'],
                        "content": [{"json": result}]
                    })

            messages.append({
                "role": "user",
                "content": [{"toolResult": tr} for tr in tool_results]
            })
        elif stop_reason == 'end_turn':
            break

    print(f"\n{'='*70}\n")


if __name__ == "__main__":
    # Test with thinking model
    test_complex_reasoning("moonshot.kimi-k2-thinking")
    test_complex_reasoning("minimax.minimax-m2")
