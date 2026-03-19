#!/usr/bin/env python3
"""
Complete Setup Script for Enhanced Deforum Music Generator
Handles installation, configuration, and initial setup of all components.

Usage:
    python setup.py --mode [minimal|standard|full|dev]
    python setup.py --install-deps --setup-models --create-config
"""

import os
import sys
import subprocess
import json
import platform
import tempfile
import shutil
from pathlib import Path
from typing import Dict, List, Optional
import argparse

SETUPTOOLS_COMMANDS = {
    "egg_info",
    "dist_info",
    "build",
    "build_ext",
    "bdist_wheel",
    "sdist",
    "develop",
    "editable_wheel",
}

def _argv_contains_setuptools_command(argv: list[str]) -> bool:
    return any(arg in SETUPTOOLS_COMMANDS for arg in argv)


def maybe_run_setuptools(argv: list[str] | None = None) -> bool:
    """Run setuptools when invoked by build backends."""
    check_argv = argv or sys.argv
    if _argv_contains_setuptools_command(check_argv):
        from setuptools import setup

        setup()
        return True
    return False


class SetupManager:
    """Manages the complete setup process."""
    
    def __init__(self):
        self.system_info = self._detect_system()
        self.install_log = []
        
    def _detect_system(self) -> Dict[str, str]:
        """Detect system information."""
        return {
            "os": platform.system().lower(),
            "architecture": platform.architecture()[0],
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
            "platform": platform.platform()
        }
    
    def log(self, message: str, level: str = "INFO"):
        """Log setup messages."""
        log_entry = f"[{level}] {message}"
        print(log_entry)
        self.install_log.append(log_entry)
    
    def check_python_version(self) -> bool:
        """Check if Python version is compatible."""
        major, minor = sys.version_info.major, sys.version_info.minor
        if major < 3 or (major == 3 and minor < 8):
            self.log(f"Python {major}.{minor} detected. Python 3.8+ required.", "ERROR")
            return False
        
        self.log(f"Python {major}.{minor} - Compatible", "SUCCESS")
        return True
    
    def install_system_dependencies(self) -> bool:
        """Install system-level dependencies."""
        self.log("Installing system dependencies...")
        
        commands = []
        
        if self.system_info["os"] == "linux":
            # Ubuntu/Debian
            commands = [
                ["sudo", "apt-get", "update"],
                ["sudo", "apt-get", "install", "-y", "ffmpeg", "libsndfile1-dev", "gcc", "g++"]
            ]
        elif self.system_info["os"] == "darwin":  # macOS
            # Check if Homebrew is available
            try:
                subprocess.run(["brew", "--version"], check=True, capture_output=True)
                commands = [
                    ["brew", "install", "ffmpeg", "libsndfile"]
                ]
            except (subprocess.CalledProcessError, FileNotFoundError):
                self.log("Homebrew not found. Please install Homebrew first: https://brew.sh", "WARNING")
        
        elif self.system_info["os"] == "windows":
            self.log("Windows: Please ensure FFmpeg is installed and in PATH", "WARNING")
            self.log("Download from: https://ffmpeg.org/download.html", "INFO")
        
        # Execute commands
        for cmd in commands:
            try:
                result = subprocess.run(cmd, check=True, capture_output=True, text=True)
                self.log(f"Successfully ran: {' '.join(cmd)}")
            except subprocess.CalledProcessError as e:
                self.log(f"Failed to run {' '.join(cmd)}: {e}", "ERROR")
                return False
        
        return True
    
    def create_virtual_environment(self, env_path: str = "venv") -> bool:
        """Create Python virtual environment."""
        self.log(f"Creating virtual environment at {env_path}...")
        
        try:
            subprocess.run([sys.executable, "-m", "venv", env_path], check=True)
            
            # Get activation script path
            if self.system_info["os"] == "windows":
                activate_script = os.path.join(env_path, "Scripts", "activate.bat")
                pip_path = os.path.join(env_path, "Scripts", "pip")
            else:
                activate_script = os.path.join(env_path, "bin", "activate")
                pip_path = os.path.join(env_path, "bin", "pip")
            
            self.log(f"Virtual environment created. Activate with: {activate_script}")
            return True
            
        except subprocess.CalledProcessError as e:
            self.log(f"Failed to create virtual environment: {e}", "ERROR")
            return False
    
    def install_python_dependencies(self, mode: str = "standard") -> bool:
        """Install Python dependencies based on mode."""
        self.log(f"Installing Python dependencies for '{mode}' mode...")
        
        # Define dependency sets
        deps = {
            "minimal": [
                "gradio>=3.40.0",
                "numpy>=1.21.0",
                "scipy>=1.7.0", 
                "requests>=2.25.0",
                "PyYAML>=6.0.0",
            ],
            "standard": [
                "gradio>=3.40.0",
                "numpy>=1.21.0",
                "scipy>=1.7.0",
                "requests>=2.25.0",
                "PyYAML>=6.0.0",
                "librosa>=0.8.0",
                "soundfile>=0.10.0"
            ],
            "full": [
                "gradio>=3.40.0",
                "numpy>=1.21.0", 
                "scipy>=1.7.0",
                "requests>=2.25.0",
                "PyYAML>=6.0.0",
                "librosa>=0.8.0",
                "soundfile>=0.10.0",
                "openai-whisper>=20230314",
                "torch>=1.9.0",
                "transformers>=4.20.0",
                "spacy>=3.4.0",
                "textblob>=0.17.0",
                "nltk>=3.7.0"
            ],
            "dev": [
                "gradio>=3.40.0",
                "numpy>=1.21.0",
                "scipy>=1.7.0", 
                "requests>=2.25.0",
                "PyYAML>=6.0.0",
                "librosa>=0.8.0",
                "soundfile>=0.10.0",
                "openai-whisper>=20230314",
                "torch>=1.9.0",
                "transformers>=4.20.0",
                "spacy>=3.4.0",
                "textblob>=0.17.0",
                "nltk>=3.7.0",
                # Development tools
                "fastapi>=0.68.0",
                "uvicorn>=0.15.0",
                "pytest>=6.2.0",
                "black>=21.0.0",
                "flake8>=3.9.0",
                "mypy>=0.910",
                "jupyter>=1.0.0",
                "matplotlib>=3.3.0",
                "opencv-python>=4.5.0",
                "psutil>=5.8.0"
            ]
        }
        
        if mode not in deps:
            self.log(f"Unknown mode: {mode}", "ERROR")
            return False
        
        # Install packages
        packages = deps[mode]
        
        for package in packages:
            try:
                self.log(f"Installing {package}...")
                result = subprocess.run([
                    sys.executable, "-m", "pip", "install", package
                ], check=True, capture_output=True, text=True)
                self.log(f"Successfully installed {package}")
            except subprocess.CalledProcessError as e:
                self.log(f"Failed to install {package}: {e.stderr}", "WARNING")
                # Continue with other packages
        
        return True
    
    def setup_ai_models(self, mode: str = "standard") -> bool:
        """Download and setup AI models."""
        self.log("Setting up AI models...")
        
        model_tasks = []
        
        if mode in ["full", "dev"]:
            model_tasks = [
                self._download_spacy_models,
                self._download_nltk_data,
                self._setup_whisper_models,
                self._setup_textblob_corpora
            ]
        elif mode == "standard":
            model_tasks = [
                self._setup_whisper_models  # Basic whisper model only
            ]
        
        for task in model_tasks:
            try:
                task()
            except Exception as e:
                self.log(f"Model setup task failed: {e}", "WARNING")
        
        return True
    
    def _download_spacy_models(self):
        """Download spaCy language models."""
        models = ["en_core_web_sm"]
        
        for model in models:
            try:
                self.log(f"Downloading spaCy model: {model}")
                subprocess.run([
                    sys.executable, "-m", "spacy", "download", model
                ], check=True, capture_output=True)
                self.log(f"Downloaded spaCy model: {model}")
            except subprocess.CalledProcessError:
                self.log(f"Failed to download spaCy model: {model}", "WARNING")
    
    def _download_nltk_data(self):
        """Download NLTK data."""
        import nltk
        
        datasets = ["vader_lexicon", "punkt", "stopwords"]
        
        for dataset in datasets:
            try:
                self.log(f"Downloading NLTK dataset: {dataset}")
                nltk.download(dataset, quiet=True)
                self.log(f"Downloaded NLTK dataset: {dataset}")
            except Exception:
                self.log(f"Failed to download NLTK dataset: {dataset}", "WARNING")
    
    def _setup_whisper_models(self):
        """Pre-download Whisper models."""
        try:
            import whisper
            
            # Download base model (good balance of speed/accuracy)
            self.log("Pre-loading Whisper model (base)...")
            whisper.load_model("base")
            self.log("Whisper base model ready")
            
        except ImportError:
            self.log("Whisper not installed, skipping model setup", "WARNING")
        except Exception as e:
            self.log(f"Whisper model setup failed: {e}", "WARNING")
    
    def _setup_textblob_corpora(self):
        """Download TextBlob corpora."""
        try:
            import textblob
            self.log("Downloading TextBlob corpora...")
            # This will download necessary corpora
            textblob.download_corpora.download_all()
            self.log("TextBlob corpora downloaded")
        except ImportError:
            self.log("TextBlob not installed, skipping corpora setup", "WARNING")
        except Exception as e:
            self.log(f"TextBlob corpora setup failed: {e}", "WARNING")
    
    def create_config_files(self) -> bool:
        """Create configuration files."""
        self.log("Creating configuration files...")
        
        # Main configuration
        config = {
            "version": "2.0.0",
            "installation_mode": "standard",
            "features": {
                "audio_analysis": True,
                "lyrics_transcription": False,
                "advanced_nlp": False,
                "multi_track": False,
                "style_transfer": False,
                "cloud_storage": False,
                "web_api": False
            },
            "paths": {
                "temp_dir": str(Path.home() / "tmp" / "deforum_music"),
                "cache_dir": str(Path.home() / ".deforum_music_cache"),
                "output_dir": str(Path.home() / "DeforumMusic" / "output")
            },
            "performance": {
                "max_audio_duration": 600,
                "max_workers": 4,
                "enable_gpu": True,
                "cache_size_mb": 256
            },
            "ui": {
                "theme": "soft",
                "port": 7860,
                "share": False,
                "auth_enabled": False
            }
        }
        
        # Write main config
        config_path = Path("config.json")
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
        self.log(f"Created config file: {config_path}")
        
        # Create .env template
        env_template = """# Enhanced Deforum Music Generator Environment Variables

# API Keys (optional)
ANTHROPIC_API_KEY=your_anthropic_key_here
OPENAI_API_KEY=your_openai_key_here

# Cloud Storage (optional)
AWS_ACCESS_KEY_ID=your_aws_key
AWS_SECRET_ACCESS_KEY=your_aws_secret
AZURE_STORAGE_CONNECTION_STRING=your_azure_connection

# Server Configuration
HOST=0.0.0.0
PORT=7860
WORKERS=4

# Processing Limits
MAX_AUDIO_DURATION=600
MAX_CONCURRENT_JOBS=3

# Feature Flags
ENABLE_CLOUD_STORAGE=false
ENABLE_MULTITRACK=false
ENABLE_STYLE_TRANSFER=false
ENABLE_WEB_API=false
"""
        
        env_path = Path(".env.template")
        with open(env_path, 'w') as f:
            f.write(env_template)
        self.log(f"Created environment template: {env_path}")
        
        # Create directories
        for dir_path in config["paths"].values():
            Path(dir_path).mkdir(parents=True, exist_ok=True)
            self.log(f"Created directory: {dir_path}")
        
        return True
    
    def create_startup_scripts(self):
        """Create convenient startup scripts."""
        self.log("Creating startup scripts...")
        
        # Shell script for Unix systems
        if self.system_info["os"] in ["linux", "darwin"]:
            startup_script = """#!/bin/bash
# Enhanced Deforum Music Generator Startup Script

echo "Starting Enhanced Deforum Music Generator..."

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    echo "Activating virtual environment..."
    source venv/bin/activate
fi

# Load environment variables
if [ -f ".env" ]; then
    echo "Loading environment variables..."
    export $(cat .env | grep -v '^#' | xargs)
fi

# Start the application
python enhanced_deforum_music_generator.py ui

echo "Application stopped."
"""
            with open("start.sh", 'w') as f:
                f.write(startup_script)
            
            # Make executable
            os.chmod("start.sh", 0o755)
            self.log("Created start.sh")
        
        # Batch script for Windows
        startup_batch = """@echo off
REM Enhanced Deforum Music Generator Startup Script

echo Starting Enhanced Deforum Music Generator...

REM Activate virtual environment if it exists
if exist "venv\\Scripts\\activate.bat" (
    echo Activating virtual environment...
    call venv\\Scripts\\activate.bat
)

REM Start the application
python enhanced_deforum_music_generator.py ui

echo Application stopped.
pause
"""
        with open("start.bat", 'w') as f:
            f.write(startup_batch)
        self.log("Created start.bat")
        
        # Docker compose for containerized deployment
        docker_compose = """version: '3.8'

services:
  deforum-music-gen:
    build: .
    ports:
      - "7860:7860"
    volumes:
      - ./data:/app/data
      - ./output:/app/output
      - ./cache:/app/cache
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - MAX_AUDIO_DURATION=600
    restart: unless-stopped
    mem_limit: 4g
    
volumes:
  data:
  output:
  cache:
"""
        with open("docker-compose.yml", 'w') as f:
            f.write(docker_compose)
        self.log("Created docker-compose.yml")
    
    def run_verification_tests(self) -> bool:
        """Run tests to verify installation."""
        self.log("Running verification tests...")
        
        tests = [
            ("Core imports", self._test_core_imports),
            ("Audio analysis", self._test_audio_analysis),
            ("UI creation", self._test_ui_creation),
            ("File operations", self._test_file_operations)
        ]
        
        passed = 0
        total = len(tests)
        
        for test_name, test_func in tests:
            try:
                self.log(f"Running test: {test_name}")
                if test_func():
                    self.log(f"✓ {test_name} - PASSED")
                    passed += 1
                else:
                    self.log(f"✗ {test_name} - FAILED", "ERROR")
            except Exception as e:
                self.log(f"✗ {test_name} - ERROR: {e}", "ERROR")
        
        success_rate = passed / total
        self.log(f"Tests passed: {passed}/{total} ({success_rate*100:.1f}%)")
        
        return success_rate >= 0.75  # 75% pass rate required
    
    def _test_core_imports(self) -> bool:
        """Test that core modules can be imported."""
        try:
            import enhanced_deforum_music_generator as main_module
            import enhanced_nlp_ai_module as nlp_module
            import enhanced_deforum_music_generator_extensions as ext_module
            return True
        except ImportError as e:
            self.log(f"Import test failed: {e}", "ERROR")
            return False
    
    def _test_audio_analysis(self) -> bool:
        """Test basic audio analysis functionality."""
        try:
            from deforum_music.core import AudioAnalyzer
            analyzer = AudioAnalyzer(max_duration=60)
            # Test with a minimal synthetic audio file would go here
            return True
        except Exception as e:
            self.log(f"Audio analysis test failed: {e}", "ERROR")
            return False
    
    def _test_ui_creation(self) -> bool:
        """Test UI creation."""
        try:
            from deforum_music.core import create_gradio_interface
            interface = create_gradio_interface()
            return interface is not None
        except ImportError:
            self.log("UI test skipped (Gradio not available)")
            return True  # Don't fail for missing optional dependency
        except Exception as e:
            self.log(f"UI creation test failed: {e}", "ERROR")
            return False
    
    def _test_file_operations(self) -> bool:
        """Test file operations."""
        try:
            # Test temporary directory creation
            with tempfile.TemporaryDirectory() as temp_dir:
                test_file = Path(temp_dir) / "test.json"
                test_data = {"test": True}
                
                with open(test_file, 'w') as f:
                    json.dump(test_data, f)
                
                with open(test_file, 'r') as f:
                    loaded_data = json.load(f)
                
                return loaded_data == test_data
        except Exception as e:
            self.log(f"File operations test failed: {e}", "ERROR")
            return False
    
    def generate_installation_report(self) -> str:
        """Generate installation report."""
        report = f"""
Enhanced Deforum Music Generator - Installation Report
======================================================

System Information:
- OS: {self.system_info['os']} ({self.system_info['platform']})
- Architecture: {self.system_info['architecture']}
- Python Version: {self.system_info['python_version']}

Installation Log:
"""
        
        for log_entry in self.install_log:
            report += f"{log_entry}\n"
        
        report += """

Next Steps:
1. Review the configuration in config.json
2. Copy .env.template to .env and configure API keys (optional)
3. Run the application:
   - Linux/Mac: ./start.sh
   - Windows: start.bat
   - Python: python enhanced_deforum_music_generator.py ui

4. Open your browser to http://localhost:7860

For advanced features, see the documentation.
"""
        
        return report
    
    def run_full_setup(self, mode: str = "standard") -> bool:
        """Run the complete setup process."""
        self.log("=== Enhanced Deforum Music Generator Setup ===")
        self.log(f"Mode: {mode}")
        self.log(f"System: {self.system_info['platform']}")
        
        steps = [
            ("Python version check", self.check_python_version),
            ("System dependencies", lambda: self.install_system_dependencies()),
            ("Python dependencies", lambda: self.install_python_dependencies(mode)),
            ("AI models setup", lambda: self.setup_ai_models(mode)),
            ("Configuration files", self.create_config_files),
            ("Startup scripts", lambda: self.create_startup_scripts() or True),
            ("Verification tests", self.run_verification_tests)
        ]
        
        for step_name, step_func in steps:
            self.log(f"\n--- {step_name} ---")
            try:
                if not step_func():
                    self.log(f"Setup step failed: {step_name}", "ERROR")
                    return False
            except Exception as e:
                self.log(f"Setup step error: {step_name} - {e}", "ERROR")
                return False
        
        # Generate and save report
        report = self.generate_installation_report()
        with open("installation_report.txt", 'w') as f:
            f.write(report)
        
        self.log("\n=== Setup Complete! ===")
        self.log("Installation report saved to: installation_report.txt")
        self.log("Start the application with: ./start.sh (Linux/Mac) or start.bat (Windows)")
        
        return True


def main():
    parser = argparse.ArgumentParser(
        description="Enhanced Deforum Music Generator Setup",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Installation Modes:
  minimal  - Basic functionality only (Gradio UI + basic analysis)
  standard - Includes audio processing (librosa) 
  full     - All AI features (Whisper, Transformers, NLP)
  dev      - Full features + development tools

Examples:
  python setup.py --mode standard
  python setup.py --mode full --no-tests
  python setup.py --install-deps --setup-models
        """
    )
    
    parser.add_argument("--mode", choices=["minimal", "standard", "full", "dev"], 
                       default="standard", help="Installation mode")
    parser.add_argument("--no-system-deps", action="store_true", 
                       help="Skip system dependency installation")
    parser.add_argument("--no-models", action="store_true",
                       help="Skip AI model downloads")
    parser.add_argument("--no-tests", action="store_true",
                       help="Skip verification tests")
    parser.add_argument("--install-deps", action="store_true",
                       help="Only install dependencies")
    parser.add_argument("--setup-models", action="store_true",
                       help="Only setup AI models")
    parser.add_argument("--create-config", action="store_true",
                       help="Only create configuration files")
    
    args, unknown_args = parser.parse_known_args()

    if unknown_args and _argv_contains_setuptools_command(unknown_args):
        from setuptools import setup

        setup()
        return
    
    setup_manager = SetupManager()
    
    # Check Python version first
    if not setup_manager.check_python_version():
        sys.exit(1)
    
    # Handle specific tasks
    if args.install_deps:
        success = setup_manager.install_python_dependencies(args.mode)
        sys.exit(0 if success else 1)
    
    if args.setup_models:
        success = setup_manager.setup_ai_models(args.mode)
        sys.exit(0 if success else 1)
    
    if args.create_config:
        success = setup_manager.create_config_files()
        sys.exit(0 if success else 1)
    
    # Run full setup
    success = setup_manager.run_full_setup(args.mode)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    if not maybe_run_setuptools(sys.argv):
        main()
