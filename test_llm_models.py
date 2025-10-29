#!/usr/bin/env python3
"""
Script to test different LLM models and capture their function call patterns.
"""

import asyncio
import json
from typing import List, Dict

# Test configurations for different models
MODEL_CONFIGS = [
    {
        "provider": "openai",
        "model": "gpt-4",
        "description": "OpenAI GPT-4"
    },
    {
        "provider": "openai", 
        "model": "gpt-4-turbo",
        "description": "OpenAI GPT-4 Turbo"
    },
    {
        "provider": "openai",
        "model": "gpt-3.5-turbo",
        "description": "OpenAI GPT-3.5 Turbo"
    },
    {
        "provider": "groq",
        "model": "mixtral-8x7b-32768",
        "description": "Groq Mixtral"
    },
    {
        "provider": "groq",
        "model": "llama3-70b-8192",
        "description": "Groq Llama 3"
    },
    {
        "provider": "google",
        "model": "gemini-pro",
        "description": "Google Gemini Pro"
    },
    {
        "provider": "azure",
        "model": "gpt-4",
        "description": "Azure OpenAI GPT-4"
    },
    {
        "provider": "dograh",
        "model": "gpt-4",
        "description": "Dograh GPT-4"
    }
]

# Test prompts that typically trigger function calls
TEST_PROMPTS = [
    "Thank you, goodbye!",
    "I need to end this call now.",
    "Transfer me to billing department.",
    "Can you hold for a moment?",
    "I'm done, you can hang up now.",
    "Connect me to a supervisor.",
    "That's all I needed, bye!",
    "Please terminate this conversation."
]

def create_test_config(model_config: Dict) -> Dict:
    """Create a test configuration for a specific model."""
    return {
        "llm": {
            "provider": model_config["provider"],
            "model": model_config["model"],
            "api_key": f"test-key-{model_config['provider']}"  # Replace with actual keys
        },
        "tts": {
            "provider": "openai",
            "model": "tts-1",
            "voice": "alloy",
            "api_key": "test-tts-key"
        },
        "stt": {
            "provider": "deepgram",
            "model": "nova-2",
            "api_key": "test-stt-key"
        }
    }

async def test_model_responses():
    """Test each model and log responses."""
    
    print("="*60)
    print("LLM MODEL FUNCTION PATTERN TEST")
    print("="*60)
    
    for model_config in MODEL_CONFIGS:
        print(f"\n📝 Testing: {model_config['description']}")
        print("-"*40)
        
        config = create_test_config(model_config)
        
        print(f"Configuration:")
        print(f"  Provider: {config['llm']['provider']}")
        print(f"  Model: {config['llm']['model']}")
        
        print("\nTest Prompts:")
        for i, prompt in enumerate(TEST_PROMPTS, 1):
            print(f"  {i}. {prompt}")
        
        print("\n⚠️  Update the configuration in your API/database to:")
        print(json.dumps(config['llm'], indent=2))
        
        input(f"\nPress Enter after testing {model_config['description']}...")
    
    print("\n✅ Testing complete!")
    print("\nNext steps:")
    print("1. Check logs: tail -f /var/log/dograh/tts_debug.log | grep TTS_DEBUG")
    print("2. Run analysis: python test_function_patterns.py")
    print("3. Review patterns found in function_patterns_*.json")

if __name__ == "__main__":
    asyncio.run(test_model_responses())