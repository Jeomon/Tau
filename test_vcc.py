#!/usr/bin/env python3
"""
Test script for VCC extension
"""

import sys
from pathlib import Path

# Add the project to the path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# Add extensions to the path
extensions_path = project_root / '.tau' / 'extensions'
sys.path.insert(0, str(extensions_path))

# Create a mock tau object to test the extension
class MockTau:
    def __init__(self):
        self.prompts = []
        self.commands = {}
        self.hooks = {}
    
    def append_prompt(self, text):
        self.prompts.append(text)
        print(f"Added prompt: {text[:50]}...")
    
    def register_command(self, name, description, handler):
        self.commands[name] = {
            'description': description,
            'handler': handler
        }
        print(f"Registered command: /{name}")
    
    def on(self, event, handler):
        if event not in self.hooks:
            self.hooks[event] = []
        self.hooks[event].append(handler)
        print(f"Registered hook for: {event}")

# Test the simple VCC extension
def test_vcc_extension():
    print("Testing VCC Extension...")
    
    try:
        # Try to import the vcc extension
        import vcc
        
        # Get the register function
        register_func = getattr(vcc, 'register', None)
        if register_func:
            tau = MockTau()
            register_func(tau)
            
            print("Extension loaded successfully!")
            print(f"Commands registered: {list(tau.commands.keys())}")
            print(f"Hooks registered: {list(tau.hooks.keys())}")
            print(f"Prompts added: {len(tau.prompts)}")
            
            return True
        else:
            print("No register function found in vcc module")
            return False
        
    except Exception as e:
        print(f"Error loading extension: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_vcc_extension()
    sys.exit(0 if success else 1)