#!/usr/bin/env python3
"""
Demo: Using boto3 Converse API with Tool Use

This demonstrates how to use AWS Bedrock's Converse API with Claude models
to implement tool calling (function calling) patterns.

Requirements:
    pip install boto3

Environment:
    AWS_REGION=us-east-1 (or your preferred region)
    AWS credentials configured via ~/.aws/credentials or environment variables
"""

import json
import boto3
from typing import Dict, Any


# Tool Definition - Weather API
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


def get_weather(city: str, units: str = "celsius") -> Dict[str, Any]:
    """
    Mock weather function - in production, this would call a real weather API
    """
    # Mock data for demonstration
    mock_weather_data = {
        "beijing": {"temp": 15, "condition": "Partly Cloudy", "humidity": 45},
        "new york": {"temp": 22, "condition": "Sunny", "humidity": 60},
        "london": {"temp": 12, "condition": "Rainy", "humidity": 80},
        "tokyo": {"temp": 18, "condition": "Clear", "humidity": 55},
    }

    city_lower = city.lower()
    if city_lower in mock_weather_data:
        weather = mock_weather_data[city_lower]
        temp = weather["temp"]

        # Convert to fahrenheit if requested
        if units == "fahrenheit":
            temp = (temp * 9/5) + 32

        return {
            "city": city,
            "temperature": temp,
            "units": units,
            "condition": weather["condition"],
            "humidity": weather["humidity"]
        }
    else:
        return {
            "error": f"Weather data not available for {city}"
        }


def process_tool_call(tool_name: str, tool_input: Dict[str, Any]) -> str:
    """
    Process a tool call and return the result as a JSON string
    """
    if tool_name == "get_weather":
        result = get_weather(**tool_input)
        return json.dumps(result)
    else:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})


def run_converse_with_tools(user_message: str, model_id: str = "moonshot.kimi-k2-thinking"):
    """
    Run a conversation with Claude using the Converse API with tool support

    Args:
        user_message: The user's input message
        model_id: AWS Bedrock model ID (default: Claude 3.5 Sonnet)
    """
    # Initialize Bedrock client
    bedrock = boto3.client('bedrock-runtime', region_name='us-east-1')

    # Initialize conversation messages
    messages = [
        {
            "role": "user",
            "content": [{"text": user_message}]
        }
    ]

    # Tool configuration
    tool_config = {
        "tools": [WEATHER_TOOL]
    }

    print(f"\n{'='*60}")
    print(f"User: {user_message}")
    print(f"{'='*60}\n")

    # Conversation loop - handle multiple tool calls
    max_iterations = 5
    response = None

    for iteration in range(max_iterations):
        print(f"--- Iteration {iteration + 1} ---")

        # Call the Converse API
        response = bedrock.converse(
            modelId=model_id,
            messages=messages,
            toolConfig=tool_config,
        )

        # Extract response details
        stop_reason = response['stopReason']
        output_message = response['output']['message']

        print(f"Stop Reason: {stop_reason}")

        # Add assistant's response to conversation history
        messages.append(output_message)

        # Print any reasoning and text content in the response
        for i, content_block in enumerate(output_message['content']):
            # Handle reasoningContent (for models like minimax)
            if 'reasoningContent' in content_block:
                print(f"\n[🧠 Reasoning Content Block {i}]:")
                reasoning_content = content_block['reasoningContent']
                if 'reasoningText' in reasoning_content:
                    print(reasoning_content['reasoningText'].get('text', ''))
                else:
                    print(json.dumps(reasoning_content, indent=2, ensure_ascii=False))
            # Handle reasoning (for other models)
            if 'reasoning' in content_block:
                print(f"\n[🧠 Reasoning Block {i}]:")
                print(content_block['reasoning'])
            if 'text' in content_block:
                print(f"\n[💬 Text Block {i}]:")
                print(content_block['text'])

        # Process the response based on stop reason
        if stop_reason == 'tool_use':
            # Claude wants to use a tool
            tool_requests = []

            for content_block in output_message['content']:
                if 'toolUse' in content_block:
                    tool_use = content_block['toolUse']
                    tool_use_id = tool_use['toolUseId']
                    tool_name = tool_use['name']
                    tool_input = tool_use['input']

                    print(f"\nTool Call: {tool_name}")
                    print(f"Tool Input: {json.dumps(tool_input, indent=2)}")

                    # Execute the tool
                    tool_result = process_tool_call(tool_name, tool_input)
                    print(f"Tool Result: {tool_result}")

                    tool_requests.append({
                        "toolUseId": tool_use_id,
                        "content": [{"json": json.loads(tool_result)}]
                    })

            # Send tool results back to Claude
            messages.append({
                "role": "user",
                "content": [{"toolResult": tr} for tr in tool_requests]
            })

        elif stop_reason == 'end_turn':
            # Claude has finished responding
            print("\nAssistant's Final Response:")
            for content_block in output_message['content']:
                if 'text' in content_block:
                    print(content_block['text'])
            break

        else:
            # Other stop reasons (max_tokens, etc.)
            print(f"Unexpected stop reason: {stop_reason}")
            break

    print(f"\n{'='*60}\n")

    # Return usage statistics
    if response is not None:
        usage = response.get('usage', {})
        print(f"Token Usage:")
        print(f"  Input Tokens: {usage.get('inputTokens', 0)}")
        print(f"  Output Tokens: {usage.get('outputTokens', 0)}")
        print(f"  Total Tokens: {usage.get('totalTokens', 0)}")


def main():
    """
    Run demo examples
    """
    print("=" * 60)
    print("AWS Bedrock Converse API - Tool Use Demo")
    print("=" * 60)

    # Test with different models
    models_to_test = [
        "moonshot.kimi-k2-thinking",
        "minimax.minimax-m2"
    ]

    for model_id in models_to_test:
        print(f"\n\n{'='*60}")
        print(f"🤖 Testing Model: {model_id}")
        print(f"{'='*60}")

        # Example 1: Single tool call
        print("\n📍 Example 1: Single Tool Call")
        run_converse_with_tools("What's the weather like in Beijing?", model_id=model_id)

        # Example 2: Multiple tool calls
        print("\n📍 Example 2: Multiple Tool Calls")
        run_converse_with_tools(
            "Can you compare the weather in New York and London? Which city is warmer?",
            model_id=model_id
        )

        # Example 3: Tool call with specific units
        print("\n📍 Example 3: Tool Call with Parameters")
        run_converse_with_tools("What's the temperature in Tokyo in fahrenheit?", model_id=model_id)

        # Example 4: No tool needed
        print("\n📍 Example 4: Conversation Without Tools")
        run_converse_with_tools("What are some popular tourist attractions in Paris?", model_id=model_id)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ Error: {e}")
        print("\nMake sure you have:")
        print("  1. AWS credentials configured (aws configure)")
        print("  2. Access to Claude models in AWS Bedrock")
        print("  3. boto3 installed (pip install boto3)")
