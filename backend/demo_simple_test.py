#!/usr/bin/env python3
"""
Simplified demo showing text output in each iteration
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
                    "city": {
                        "type": "string",
                        "description": "The city name (e.g., 'Beijing', 'New York')",
                    },
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


def get_weather(city: str, units: str = "celsius"):
    """Mock weather function"""
    mock_data = {
        "beijing": {"temp": 15, "condition": "Partly Cloudy", "humidity": 45},
        "new york": {"temp": 22, "condition": "Sunny", "humidity": 60},
    }

    city_lower = city.lower()
    if city_lower in mock_data:
        weather = mock_data[city_lower]
        temp = weather["temp"] if units == "celsius" else (weather["temp"] * 9/5) + 32
        return {
            "city": city,
            "temperature": temp,
            "units": units,
            "condition": weather["condition"],
            "humidity": weather["humidity"]
        }
    return {"error": f"Weather data not available for {city}"}


def test_model(model_id: str):
    """Test a model with a simple query"""
    bedrock = boto3.client('bedrock-runtime', region_name='us-east-1')

    messages = [{"role": "user", "content": [{"text": "Use the get_weather tool to check the current weather in Beijing."}]}]
    tool_config = {"tools": [WEATHER_TOOL]}

    print(f"\n{'='*60}")
    print(f"Model: {model_id}")
    print(f"{'='*60}\n")

    for iteration in range(3):
        print(f"--- Iteration {iteration + 1} ---")

        response = bedrock.converse(
            modelId=model_id,
            messages=messages,
            toolConfig=tool_config,
        )

        stop_reason = response['stopReason']
        output_message = response['output']['message']

        print(f"Stop Reason: {stop_reason}\n")

        # Print all content blocks
        for i, content_block in enumerate(output_message['content']):
            # Handle reasoningContent (for models like minimax)
            if 'reasoningContent' in content_block:
                print(f"[🧠 Reasoning Content Block {i}]:")
                reasoning_content = content_block['reasoningContent']
                if 'reasoningText' in reasoning_content:
                    print(reasoning_content['reasoningText'].get('text', ''))
                else:
                    print(json.dumps(reasoning_content, indent=2, ensure_ascii=False))
                print()

            # Handle reasoning (for other models)
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
                print(f"[Tool Use Block {i}]:")
                print(f"  Tool: {tool_use['name']}")
                print(f"  Input: {json.dumps(tool_use['input'])}")
                print()

        messages.append(output_message)

        if stop_reason == 'tool_use':
            # Execute tools and send results back
            tool_results = []
            for content_block in output_message['content']:
                if 'toolUse' in content_block:
                    tool_use = content_block['toolUse']
                    result = get_weather(**tool_use['input'])
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

    print(f"{'='*60}\n")


if __name__ == "__main__":
    # Test both models
    test_model("moonshot.kimi-k2-thinking")
    test_model("minimax.minimax-m2")
