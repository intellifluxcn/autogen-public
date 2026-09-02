import sys
import subprocess
import os
from pathlib import Path

def check_python_version():
    if sys.version_info < (3, 11):
        print("❌ Python 3.11+ required")
        return False
    print("✓ Python version OK")
    return True

def check_node_npm():
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
        subprocess.run(["npm", "--version"], check=True, capture_output=True)
        print("✓ Node.js and npm installed")
        return True
    except:
        print("❌ Node.js or npm not found")
        return False

def check_playwright():
    try:
        subprocess.run(["playwright", "show-browser"], capture_output=True)
        print("✓ Playwright installed")
        return True
    except:
        print("❌ Playwright not installed. Run: playwright install")
        return False

def check_env_file():
    env_path = Path(".env")
    if not env_path.exists():
        print("❌ .env file not found")
        return False
    
    with open(env_path) as f:
        content = f.read()
        if "OPENROUTER_API_KEY" not in content:
            print("❌ OPENROUTER_API_KEY not found in .env")
            return False
    
    print("✓ .env file configured")
    return True

def run_all_checks():
    print("\n=== Running Preflight Checks ===\n")
    
    checks = [
        check_python_version(),
        check_node_npm(),
        check_playwright(),
        check_env_file()
    ]
    
    if all(checks):
        print("\n✓ All preflight checks passed!\n")
        return True
    else:
        print("\n❌ Some preflight checks failed. Fix errors above.\n")
        sys.exit(1)

if __name__ == "__main__":
    run_all_checks()
