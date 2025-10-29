#!/usr/bin/env python3
"""
Test script to capture function call patterns from different LLM models.
Run test calls with various scenarios to identify patterns.
"""

import asyncio
import json
from datetime import datetime
from typing import List, Dict

# Test scenarios that typically trigger function calls
TEST_SCENARIOS = [
    {
        "name": "End Call Scenario",
        "user_message": "Thank you, that's all I need. Goodbye!",
        "expected_function": "end_call"
    },
    {
        "name": "Transfer Call Scenario", 
        "user_message": "I need to speak to a supervisor please.",
        "expected_function": "transfer_call"
    },
    {
        "name": "Hold Call Scenario",
        "user_message": "Can you hold on a moment? Someone's at the door.",
        "expected_function": "hold_call"
    },
    {
        "name": "Multiple Functions",
        "user_message": "Please transfer me to billing and then end the call.",
        "expected_function": "multiple"
    },
    {
        "name": "Complex Query with Function",
        "user_message": "I've got all the information I need about my order. Thanks for your help, bye!",
        "expected_function": "end_call"
    }
]

# Different LLM models to test
LLM_MODELS = [
    "gpt-4",
    "gpt-4-turbo", 
    "gpt-3.5-turbo",
    # Add other models you're using
]

def analyze_logs(log_file: str = "/var/log/dograh/tts_debug.log") -> Dict:
    """
    Analyze the debug logs to find function patterns.
    """
    patterns_found = {
        "function_formats": set(),
        "common_phrases": [],
        "by_model": {},
        "by_scenario": {}
    }
    
    try:
        with open(log_file, 'r') as f:
            for line in f:
                if "[TTS_DEBUG] Potential function leak detected:" in line:
                    # Extract the problematic text
                    text = line.split("detected: '")[1].rstrip("'\n")
                    
                    # Identify patterns
                    if "function=" in text:
                        patterns_found["function_formats"].add("function=FUNCNAME")
                    if "function:" in text:
                        patterns_found["function_formats"].add("function:FUNCNAME")
                    if "move to function" in text:
                        patterns_found["function_formats"].add("move to function FUNCNAME")
                    if "[function" in text:
                        patterns_found["function_formats"].add("[function:FUNCNAME]")
                    if "{function" in text:
                        patterns_found["function_formats"].add("{function:FUNCNAME}")
                    
                    patterns_found["common_phrases"].append(text)
    
    except FileNotFoundError:
        print(f"Log file {log_file} not found. Make sure logging is enabled.")
    
    return patterns_found

def print_analysis_report(patterns: Dict):
    """
    Print a formatted report of found patterns.
    """
    print("\n" + "="*60)
    print("FUNCTION PATTERN ANALYSIS REPORT")
    print("="*60)
    
    print("\n1. DETECTED FUNCTION FORMATS:")
    for format_pattern in patterns["function_formats"]:
        print(f"   - {format_pattern}")
    
    print("\n2. SAMPLE PHRASES WITH FUNCTIONS:")
    for i, phrase in enumerate(patterns["common_phrases"][:10], 1):
        print(f"   {i}. {phrase}")
    
    print("\n3. RECOMMENDED REGEX PATTERNS:")
    print("   Based on the analysis, include these patterns in your filter:")
    
    # Generate regex patterns based on findings
    regex_patterns = [
        r'\bfunction\s*=\s*\w+',
        r'\bfunction\s*:\s*\w+', 
        r'\bmove\s+to\s+function\s+\w+',
        r'\[function[:=]\s*\w+\]',
        r'\{function[:=]\s*\w+\}',
    ]
    
    for pattern in regex_patterns:
        print(f"   - r'{pattern}'")

async def main():
    """
    Main function to coordinate testing.
    """
    print("Function Pattern Detection Test")
    print("-" * 40)
    print("\nThis test will:")
    print("1. Run multiple call scenarios")
    print("2. Test with different LLM models")
    print("3. Capture function patterns in responses")
    print("4. Generate a report of patterns to filter")
    
    print("\n⚠️  Make sure:")
    print("   - The logging changes are deployed")
    print("   - You have access to different LLM models")
    print("   - The system is configured to log to a file")
    
    input("\nPress Enter to start testing...")
    
    # Instructions for manual testing
    print("\n📋 MANUAL TEST INSTRUCTIONS:")
    print("-" * 40)
    for i, scenario in enumerate(TEST_SCENARIOS, 1):
        print(f"\n{i}. {scenario['name']}:")
        print(f"   User says: '{scenario['user_message']}'")
        print(f"   Expected: Bot should {scenario['expected_function']}")
        print("   ✓ Make the call and note any function text spoken")
    
    print("\n💡 TIP: Test with different models by changing config:")
    for model in LLM_MODELS:
        print(f"   - {model}")
    
    input("\nPress Enter after completing test calls...")
    
    # Analyze the logs
    print("\n📊 Analyzing logs...")
    patterns = analyze_logs()
    print_analysis_report(patterns)
    
    # Save results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"function_patterns_{timestamp}.json"
    with open(output_file, 'w') as f:
        json.dump({
            "patterns": list(patterns["function_formats"]),
            "samples": patterns["common_phrases"][:20]
        }, f, indent=2)
    
    print(f"\n✅ Results saved to: {output_file}")

if __name__ == "__main__":
    asyncio.run(main())