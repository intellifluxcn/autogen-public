#!/usr/bin/env python3
"""
Unified startup script for the AutoGen Research Pipeline Web UI.

This script starts the backend and provides instructions for the frontend,
with comprehensive error checking and guidance.
"""

import subprocess
import sys
import os
import time
import signal
import argparse
from pathlib import Path
import logging

from utils.logging_config import configure_logging

configure_logging()

logging.getLogger("autogen_core").setLevel(logging.WARNING)
logging.getLogger("autogen_core.events").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)  
logging.getLogger("playwright").setLevel(logging.WARNING)  

# env_log_level = os.getenv("LOG_LEVEL", "INFO")
# try:
#     logging.getLogger().setLevel(getattr(logging, env_log_level.upper()))
# except AttributeError:
#     pass



def check_dependencies():
    """Check if required dependencies are installed."""
    missing_deps = []
    
    # Check Python dependencies
    try:
        import fastapi
        import uvicorn
        import socketio
        print("✓ Backend dependencies found")
    except ImportError as e:
        missing_deps.append(f"Backend: {e}")
    
    # Check if frontend directory exists
    frontend_dir = Path(__file__).parent / "web_ui" / "frontend"
    if not frontend_dir.exists():
        missing_deps.append("Frontend directory not found")
    elif not (frontend_dir / "package.json").exists():
        missing_deps.append("Frontend package.json not found")
    else:
        print("✓ Frontend structure found")
    
    if missing_deps:
        print("❌ Missing dependencies:")
        for dep in missing_deps:
            print(f"   - {dep}")
        print("\nTo fix:")
        print("   poetry install")
        print("   cd web_ui/frontend && npm install")
        return False
    
    return True

def start_backend():
    """Start the FastAPI backend server."""
    backend_dir = Path(__file__).parent / "web_ui" / "backend"
    
    if not backend_dir.exists():
        print("❌ Backend directory not found")
        return None
    
    # Change to backend directory
    # original_cwd = os.getcwd()
    # os.chdir(backend_dir)
    
    print("🚀 Starting backend in full mode...")
    print("📍 Backend URL: http://localhost:8000")
    print("📚 API Documentation: http://localhost:8000/docs")
    print("🔍 Health Check: http://localhost:8000/api/health")
    
    try:
        # Start full backend with Socket.IO
        cmd = [
            sys.executable, "-m", "uvicorn",
            "main:socket_app",
            "--app-dir", str(backend_dir),
            "--host", "0.0.0.0",
            "--port", "8000",
            "--log-level", "info"
        ]
        
        process = subprocess.Popen(cmd)
        
        # Restore original directory
        # os.chdir(original_cwd)
        
        return process
        
    except Exception as e:
        print(f"❌ Failed to start backend: {e}")
        # os.chdir(original_cwd)
        return None

def print_frontend_instructions():
    """Print instructions for starting the frontend."""
    frontend_dir = Path(__file__).parent / "web_ui" / "frontend"
    
    print("\n" + "=" * 60)
    print("FRONTEND SETUP - FOLLOW THESE STEPS")
    print("=" * 60)
    print("📁 1. Open a new terminal")
    print()
    print("📂 2. Navigate to frontend directory:")
    print(f"   cd {frontend_dir}")
    print()
    print("📦 3. Install dependencies (if not done yet):")
    print("   npm install")
    print()
    print("🚀 4. Start the development server:")
    print("   npm run dev")
    print()
    print("🌐 5. Open your browser to:")
    print("   http://localhost:3000")
    print()
    print("The frontend will connect to the backend automatically.")
    print("You can create projects and watch them progress through the pipeline!")
    print("=" * 60)

def test_backend_connection():
    """Test if the backend is responding."""
    import time
    import urllib.request
    import urllib.error
    
    print("🔄 Testing backend connection...")
    
    for i in range(10):  # Try for 10 seconds
        try:
            with urllib.request.urlopen("http://localhost:8000/api/health", timeout=2) as response:
                if response.status == 200:
                    print("✓ Backend is responding!")
                    return True
        except (urllib.error.URLError, urllib.error.HTTPError, OSError):
            pass
        
        if i < 9:  # Don't sleep on last iteration
            time.sleep(1)
    
    print("⚠️ Backend not responding, but it might still be starting...")
    return False

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Start AutoGen Web UI')
    parser.add_argument('--test-only', action='store_true',
                       help='Only test the backend, don\'t start it')
    
    args = parser.parse_args()
    
    print("AutoGen Research Pipeline Web UI")
    print("=" * 50)
    
    # Test only mode
    if args.test_only:
        success = test_backend_connection()
        if success:
            print("✓ Backend is running and accessible")
        else:
            print("❌ Backend is not accessible")
        return 0 if success else 1
    
    # Check dependencies
    if not check_dependencies():
        return 1
    
    # Start backend
    backend_process = None
    try:
        backend_process = start_backend()
        
        if not backend_process:
            return 1
        
        # Wait a moment for backend to start
        time.sleep(3)
        
        # Test connection
        test_backend_connection()
        
        # Print frontend instructions
        print_frontend_instructions()
        
        print("\n🎯 Backend is running. Follow the frontend instructions above.")
        print("   Press Ctrl+C to stop the backend when done.")
        
        # Wait for interrupt
        try:
            backend_process.wait()
        except KeyboardInterrupt:
            print("\n🛑 Stopping backend...")
            backend_process.terminate()
            
            # Give it time to shut down gracefully
            try:
                backend_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                print("🔨 Force killing backend...")
                backend_process.kill()
            
            print("👋 Backend stopped.")
        
    except KeyboardInterrupt:
        print("\n🛑 Interrupted during startup")
        if backend_process:
            backend_process.terminate()
    except Exception as e:
        print(f"❌ Error: {e}")
        if backend_process:
            backend_process.terminate()
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
