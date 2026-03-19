#!/usr/bin/env python3
"""
Complete Deployment Automation Script for Enhanced Deforum Music Generator
Handles the entire deployment pipeline from setup to production deployment.

Usage
    python deploy.py --target [local|docker|aws|k8s] --environment [dev|staging|prod]
    python deploy.py --setup-project --target local
    python deploy.py --build-all --push-registry
"""
import os
import sys
from pathlib import Path

# 🔧 Ensure "src" is added to sys.path so imports work
project_root = Path(__file__).resolve().parent
src_path = project_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

import json
import yaml
import shutil
import subprocess
import tempfile
import argparse
import logging
from typing import Dict, List, Optional, Any
import time

# ✅ Now this will work
from enhanced_deforum_music_generator.config.config_system import Config


# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class DeploymentManager:
    """Manages complete deployment pipeline."""

    def __init__(self, target: str = "local", environment: str = "dev"):
        self.target = target
        self.environment = environment
        self.project_root = Path.cwd()
        self.config = self._load_config()

        # Deployment configurations
        self.deployment_configs = {
            "local": self._get_default_config,
            "docker": self._get_docker_compose,
            "aws": self._get_k8s_configmap,
            "k8s": self._get_k8s_configmap,
            "azure": self._get_default_config,
        }

    def _load_config(self) -> Dict[str, Any]:
        """Load deployment configuration."""
        config_file = self.project_root / "config.json"
        if config_file.exists():
            with open(config_file, "r", encoding="utf-8") as f:
                try:
                    return json.load(f)
                except json.JSONDecodeError:
                    logger.warning("config.json exists but is invalid JSON; regenerating defaults.")
        return self._get_default_config()

    def _get_default_config(self) -> Dict[str, Any]:
        """Get default configuration."""
        return {
            "version": "2.0.0",
            "app_name": "enhanced_deforum_music_generator",
            "docker_registry": "your-registry.com",
            "environments": {
                "dev": {"replicas": 1, "resources": {"cpu": "1", "memory": "2Gi"}},
                "staging": {"replicas": 2, "resources": {"cpu": "2", "memory": "4Gi"}},
                "prod": {"replicas": 3, "resources": {"cpu": "4", "memory": "8Gi"}},
            },
            "features": {
                "audio_analysis": True,
                "lyrics_transcription": True,
                "advanced_nlp": True,
                "web_api": True,
            },
        }

    def create_project_structure(self) -> bool:
        """Create complete project structure."""
        logger.info("Creating project structure...")

        directories = [
            "src",
            "scripts",
            "tests",
            "docs",
            "deployment/docker",
            "deployment/kubernetes",
            "deployment/aws",
            "deployment/azure",
            "deployment/monitoring",
            "deployment/nginx/ssl",
            "integrations/automatic1111",
            "integrations/comfyui",
            "integrations/desktop",
            "integrations/cloud",
            "presets/styles",
            "presets/mappings",
            "presets/templates",
            "data/models",
            "data/cache",
            "data/logs",
            "data/temp",
            "output/packages",
            "output/analysis",
            "output/previews",
            "tools",
        ]

        for directory in directories:
            dir_path = self.project_root / directory
            dir_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created directory: {directory}")

        self._create_essential_files()
        return True

    def _create_essential_files(self):
        """Create essential configuration files."""
        files_to_create = {
            ".gitignore": self._get_gitignore_content(),
            ".dockerignore": self._get_dockerignore_content(),
            "requirements.txt": self._get_requirements_content(),
            "requirements-minimal.txt": self._get_minimal_requirements(),
            "requirements-full.txt": self._get_full_requirements(),
            "config.json": json.dumps(self.config, indent=2),
            ".env.template": self._get_env_template(),
            "docker-compose.yml": self._get_docker_compose(),
            "start.sh": self._get_startup_script("unix"),
            "start.bat": self._get_startup_script("windows"),
        }

                # Ensure custom.yaml exists (either root or config folder)
        custom_yaml_root = self.project_root / "src" / "enhanced_deforum_music_generator" / "custom.yaml"
        custom_yaml_config = self.project_root / "src" / "enhanced_deforum_music_generator" / "config" / "custom.yaml"
        vendored_yaml_root = self.project_root / "studio" / "edmg-studio" / "python_backend" / "enhanced_deforum_music_generator" / "custom.yaml"
        vendored_yaml_config = self.project_root / "studio" / "edmg-studio" / "python_backend" / "enhanced_deforum_music_generator" / "config" / "custom.yaml"

        if not any(p.exists() for p in (custom_yaml_root, custom_yaml_config, vendored_yaml_root, vendored_yaml_config)):
            logger.info("No custom.yaml found. Generating default one...")
            default_yaml = """# Default custom.yaml for Enhanced Deforum Music Generator

interface:
  server_port: 7860
  auto_open_browser: true
  enable_sharing: false
  show_error_details: true

generation:
  fps: 30
  duration: 60
  resolution: "512x512"
  sampler_name: "Euler a"
  steps: 20
  negative_prompt: ""

advanced:
  enable_multitrack: false
  enable_style_transfer: false
  enable_preview: true
  enable_cloud_storage: false
  cloud_provider: "local"
"""
            # Write into root
            custom_yaml_root.write_text(default_yaml)
            logger.info(f"Created default custom.yaml at {custom_yaml_root}")
 
        
        for filename, content in files_to_create.items():
            file_path = self.project_root / filename
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

            # Make shell scripts executable
            if filename.endswith(".sh"):
                try:
                    os.chmod(file_path, 0o755)
                except Exception:
                    pass

            logger.info(f"Created file: {filename}")

    def _get_gitignore_content(self) -> str:
        return """# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
*.egg-info/
.installed.cfg
*.egg

# Virtual environments
venv/
env/
ENV/

# IDE
.vscode/
.idea/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Application specific
data/models/
data/cache/
data/temp/
data/logs/
output/
*.wav
*.mp3
*.mp4
*.avi

# Configuration
.env
config.local.json

# Docker
.dockerignore

# Temporary files
*.tmp
*.temp
"""

    def _get_dockerignore_content(self) -> str:
        return """data/
output/
logs/
.git/
.pytest_cache/
.coverage
*.pyc
*.pyo
*.pyd
__pycache__/
.venv/
venv/
.env
README.md
docs/
tests/
"""

    def _get_requirements_content(self) -> str:
        return """# Core dependencies for Enhanced Deforum Music Generator
gradio>=4.44.0
numpy>=1.23.0
scipy>=1.10.0
requests>=2.25.0
librosa>=0.10.0
soundfile>=0.12.0

# AI/ML features
openai-whisper>=20230314
torch>=1.13.0
transformers>=4.30.0
spacy>=3.5.0
textblob>=0.17.0
nltk>=3.8.0

# Web API
fastapi>=0.95.0
uvicorn[standard]>=0.22.0

# Utilities
python-multipart>=0.0.5
python-dotenv>=0.21.0
pydantic>=1.10.0
"""

    def _get_minimal_requirements(self) -> str:
        return """gradio>=4.44.0
numpy>=1.23.0
scipy>=1.10.0
requests>=2.25.0
"""

    def _get_full_requirements(self) -> str:
        return """gradio>=4.44.0
numpy>=1.23.0
scipy>=1.10.0
requests>=2.25.0
librosa>=0.10.0
soundfile>=0.12.0
openai-whisper>=20230314
torch>=1.13.0
transformers>=4.30.0
spacy>=3.5.0
textblob>=0.17.0
nltk>=3.8.0
fastapi>=0.95.0
uvicorn[standard]>=0.22.0
matplotlib>=3.7.0
opencv-python>=4.8.0
psutil>=5.9.0
boto3>=1.28.0
azure-storage-blob>=12.16.0
"""

    def _get_env_template(self) -> str:
        return """# Enhanced Deforum Music Generator Configuration

# API Keys
ANTHROPIC_API_KEY=your_anthropic_key_here
OPENAI_API_KEY=your_openai_key_here

# Server Configuration
HOST=0.0.0.0
PORT=7860
WORKERS=4

# Processing Limits
MAX_AUDIO_DURATION=600
MAX_CONCURRENT_JOBS=3
ENABLE_GPU=true

# Cloud Storage (Optional)
AWS_ACCESS_KEY_ID=your_aws_key
AWS_SECRET_ACCESS_KEY=your_aws_secret
AWS_REGION=us-west-2
S3_BUCKET=your-bucket-name

AZURE_STORAGE_CONNECTION_STRING=your_azure_connection

# Feature Flags
ENABLE_CLOUD_STORAGE=false
ENABLE_MULTITRACK=false
ENABLE_STYLE_TRANSFER=false
ENABLE_WEB_API=true

# Development
DEBUG=false
LOG_LEVEL=INFO
ENABLE_PROFILING=false
"""

    def _get_docker_compose(self) -> str:
        return """version: '3.8'

services:
  deforum-music:
    build: 
      context: .
      dockerfile: deployment/docker/Dockerfile
    ports:
      - "7860:7860"
    volumes:
      - ./data:/app/data:rw
      - ./output:/app/output:rw
      - ./logs:/app/logs:rw
      - ./presets:/app/presets:rw
      - ./config.json:/app/config.json:ro
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - MAX_AUDIO_DURATION=${MAX_AUDIO_DURATION:-600}
      - LOG_LEVEL=${LOG_LEVEL:-INFO}
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import requests; requests.get('http://localhost:7860/health', timeout=5)"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
    deploy:
      resources:
        limits:
          memory: 4G
          cpus: '2.0'
        reservations:
          memory: 2G
          cpus: '1.0'

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./deployment/nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./deployment/nginx/ssl:/etc/nginx/ssl:ro
    depends_on:
      - deforum-music
    restart: unless-stopped
    profiles:
      - production

  redis:
    image: redis:alpine
    restart: unless-stopped
    volumes:
      - redis_data:/data
    profiles:
      - scaling

volumes:
  redis_data:
"""

    def _get_startup_script(self, platform: str) -> str:
        if platform == "unix":
            # Directly starts the Gradio interface without any extra questions.
            return """#!/bin/bash
# Enhanced Deforum Music Generator Startup Script (Gradio UI)

set -e

echo "🎵 Enhanced Deforum Music Generator 🎥"
echo "=================================="

# Check if virtual environment exists
if [ -d "venv" ]; then
    echo "📦 Activating virtual environment..."
    source venv/bin/activate
fi

# Load environment variables
if [ -f ".env" ]; then
    echo "⚙️ Loading environment variables..."
    export $(grep -v '^#' .env | xargs)
fi

# Create necessary directories
mkdir -p data/{models,cache,logs,temp} output/{packages,analysis,previews}

echo "🚀 Starting Gradio UI..."
python -c "from enhanced_deforum_music_generator.interface.gradio_interface import create_interface; app=create_interface(); app.launch(server_name='0.0.0.0', server_port=7860)"

echo "👋 Application stopped."
"""
        else:  # Windows
            return r"""@echo off
REM Enhanced Deforum Music Generator Startup Script (Gradio UI)

echo 🎵 Enhanced Deforum Music Generator 🎥
echo ==================================

REM Check if virtual environment exists
if exist "venv\Scripts\activate.bat" (
    echo 📦 Activating virtual environment...
    call venv\Scripts\activate.bat
)

REM Load environment variables if .env exists (PowerShell users can do this differently)
if exist ".env" (
    for /f "usebackq delims=" %%A in (".env") do (
        set "%%A"
    )
)

REM Create necessary directories
if not exist "data" mkdir data
if not exist "data\models" mkdir data\models
if not exist "data\cache" mkdir data\cache
if not exist "data\logs" mkdir data\logs
if not exist "output" mkdir output
if not exist "output\packages" mkdir output\packages
if not exist "output\analysis" mkdir output\analysis
if not exist "output\previews" mkdir output\previews

echo 🚀 Starting Gradio UI...
python -c "from enhanced_deforum_music_generator.interface.gradio_interface import create_interface; app=create_interface(); app.launch(server_name='0.0.0.0', server_port=7860)"

echo 👋 Application stopped.
pause
"""

    # ---------------------------
    # Local Deployment (Conda-aware)
    # ---------------------------
    def deploy_local(self) -> bool:
        """Deploy locally for development (prefers Conda env if available)."""
        logger.info("Deploying locally...")

        # Prefer Conda environment if environment-cuda.yml or environment.yml exists
        env_file = None
        for candidate in ["environment-cuda.yml", "environment.yml"]:
            if (self.project_root / candidate).exists():
                env_file = candidate
                break

        if env_file:
            logger.info(f"Using Conda environment from {env_file}")
            # Find env name inside file (fallback to deforum-music)
            env_name = "deforum-music"
            try:
                with open(self.project_root / env_file, "r", encoding="utf-8") as f:
                    doc = yaml.safe_load(f) or {}
                    if isinstance(doc, dict) and "name" in doc and doc["name"]:
                        env_name = doc["name"]
            except Exception:
                pass

            # Create or update env
            try:
                subprocess.run(
                    ["conda", "env", "update", "-n", env_name, "-f", env_file, "--prune"],
                    check=True,
                )
                logger.info(f"Updated existing Conda env: {env_name}")
            except subprocess.CalledProcessError:
                subprocess.run(
                    ["conda", "env", "create", "-n", env_name, "-f", env_file],
                    check=True,
                )
                logger.info(f"Created Conda env: {env_name}")

            logger.info(
                f"✅ Conda environment {env_name} ready. Activate with: conda activate {env_name}"
            )
        else:
            logger.info("No Conda environment file found, falling back to venv...")

            # Old venv method
            if not (self.project_root / "venv").exists():
                logger.info("Creating virtual environment with venv...")
                subprocess.run([sys.executable, "-m", "venv", "venv"], check=True)
            pip_path = "venv/bin/pip" if os.name != "nt" else "venv\\Scripts\\pip"
            logger.info("Installing requirements into venv...")
            subprocess.run([pip_path, "install", "-r", "requirements.txt"], check=True)

        logger.info("Local deployment setup complete! Use start script to launch UI.")
        return True

    def deploy_docker(self) -> bool:
        """Deploy using Docker."""
        logger.info("Deploying with Docker...")

        # Create Dockerfile (runs Gradio interface directly)
        dockerfile_content = """FROM python:3.10-slim

# System dependencies
RUN apt-get update && apt-get install -y \\
    ffmpeg \\
    libsndfile1-dev \\
    gcc \\
    g++ \\
    curl \\
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download models (best-effort)
RUN python -c "import spacy; spacy.cli.download('en_core_web_sm')" || echo "spaCy download failed"

# Create non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app

# Copy application
COPY --chown=appuser:appuser src/ ./src/
COPY --chown=appuser:appuser presets/ ./presets/
COPY --chown=appuser:appuser config.json ./

USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \\
    CMD ["python", "-c", "import requests; requests.get('http://localhost:7860/health', timeout=5)"]

EXPOSE 7860

# Launch Gradio UI directly
CMD ["python", "-c", "from enhanced_deforum_music_generator.interface.gradio_interface import create_interface; app=create_interface(); app.launch(server_name='0.0.0.0', server_port=7860)"]
"""
        dockerfile_path = self.project_root / "deployment" / "docker" / "Dockerfile"
        dockerfile_path.parent.mkdir(parents=True, exist_ok=True)

        with open(dockerfile_path, "w", encoding="utf-8") as f:
            f.write(dockerfile_content)

        # Build image
        image_tag = f"{self.config['app_name']}:{self.config['version']}"
        logger.info(f"Building Docker image: {image_tag}")

        build_cmd = ["docker", "build", "-t", image_tag, "-f", str(dockerfile_path), "."]
        result = subprocess.run(build_cmd, capture_output=True, text=True)

        if result.returncode != 0:
            logger.error(f"Docker build failed: {result.stderr}")
            return False

        logger.info("Docker image built successfully!")
        logger.info(f"Run with: docker run -p 7860:7860 {image_tag}")
        return True

    def deploy_kubernetes(self) -> bool:
        """Deploy to Kubernetes."""
        logger.info("Deploying to Kubernetes...")

        k8s_manifests = {
            "configmap.yaml": self._get_k8s_configmap(),
            "deployment.yaml": self._get_k8s_deployment(),
            "service.yaml": self._get_k8s_service(),
            "ingress.yaml": self._get_k8s_ingress(),
        }

        k8s_dir = self.project_root / "deployment" / "kubernetes"
        k8s_dir.mkdir(parents=True, exist_ok=True)

        for filename, content in k8s_manifests.items():
            with open(k8s_dir / filename, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info(f"Created {filename}")

        # Apply manifests
        logger.info("Applying Kubernetes manifests...")
        apply_cmd = ["kubectl", "apply", "-f", str(k8s_dir)]
        result = subprocess.run(apply_cmd, capture_output=True, text=True)

        if result.returncode != 0:
            logger.error(f"Kubernetes deployment failed: {result.stderr}")
            return False

        logger.info("Kubernetes deployment successful!")
        return True

    def _get_k8s_deployment(self) -> str:
        env_config = self.config["environments"][self.environment]

        return f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {self.config['app_name']}
  labels:
    app: {self.config['app_name']}
    version: {self.config['version']}
    environment: {self.environment}
spec:
  replicas: {env_config['replicas']}
  selector:
    matchLabels:
      app: {self.config['app_name']}
  template:
    metadata:
      labels:
        app: {self.config['app_name']}
    spec:
      containers:
      - name: {self.config['app_name']}
        image: {self.config['docker_registry']}/{self.config['app_name']}:{self.config['version']}
        ports:
        - containerPort: 7860
        env:
        - name: MAX_AUDIO_DURATION
          value: "600"
        - name: LOG_LEVEL
          value: "INFO"
        resources:
          limits:
            cpu: {env_config['resources']['cpu']}
            memory: {env_config['resources']['memory']}
          requests:
            cpu: "{max(1, int(str(env_config['resources']['cpu']).split()[0]))}"
            memory: {env_config['resources']['memory']}
        livenessProbe:
          httpGet:
            path: /health
            port: 7860
          initialDelaySeconds: 60
          periodSeconds: 30
        readinessProbe:
          httpGet:
            path: /health
            port: 7860
          initialDelaySeconds: 30
          periodSeconds: 10
"""

    def _get_k8s_service(self) -> str:
        return f"""apiVersion: v1
kind: Service
metadata:
  name: {self.config['app_name']}-service
  labels:
    app: {self.config['app_name']}
spec:
  selector:
    app: {self.config['app_name']}
  ports:
    - protocol: TCP
      port: 80
      targetPort: 7860
  type: ClusterIP
"""

    def _get_k8s_ingress(self) -> str:
        return f"""apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {self.config['app_name']}-ingress
  annotations:
    kubernetes.io/ingress.class: nginx
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/proxy-body-size: "100m"
spec:
  tls:
  - hosts:
    - {self.config['app_name']}.yourdomain.com
    secretName: {self.config['app_name']}-tls
  rules:
  - host: {self.config['app_name']}.yourdomain.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: {self.config['app_name']}-service
            port:
              number: 80
"""

    def _get_k8s_configmap(self) -> str:
        return f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: {self.config['app_name']}-config
data:
  MAX_AUDIO_DURATION: "600"
  LOG_LEVEL: "INFO"
  ENABLE_WEB_API: "true"
  HOST: "0.0.0.0"
  PORT: "7860"
"""

    def deploy_aws(self) -> bool:
        """Deploy to AWS using ECS."""
        logger.info("Deploying to AWS ECS...")

        # Create ECS task definition
        task_definition = {
            "family": self.config["app_name"],
            "networkMode": "awsvpc",
            "requiresCompatibilities": ["FARGATE"],
            "cpu": "2048",
            "memory": "4096",
            "executionRoleArn": f"arn:aws:iam::{self._get_aws_account_id()}:role/ecsTaskExecutionRole",
            "taskRoleArn": f"arn:aws:iam::{self._get_aws_account_id()}:role/ecsTaskRole",
            "containerDefinitions": [
                {
                    "name": self.config["app_name"],
                    "image": f"{self.config['docker_registry']}/{self.config['app_name']}:{self.config['version']}",
                    "portMappings": [{"containerPort": 7860, "protocol": "tcp"}],
                    "environment": [
                        {"name": "MAX_AUDIO_DURATION", "value": "600"},
                        {"name": "LOG_LEVEL", "value": "INFO"},
                    ],
                    "secrets": [
                        {
                            "name": "ANTHROPIC_API_KEY",
                            "valueFrom": f"arn:aws:secretsmanager:us-west-2:{self._get_aws_account_id()}:secret:anthropic-api-key",
                        }
                    ],
                    "logConfiguration": {
                        "logDriver": "awslogs",
                        "options": {
                            "awslogs-group": f"/ecs/{self.config['app_name']}",
                            "awslogs-region": "us-west-2",
                            "awslogs-stream-prefix": "ecs",
                        },
                    },
                    "healthCheck": {
                        "command": ["CMD-SHELL", "curl -f http://localhost:7860/health || exit 1"],
                        "interval": 30,
                        "timeout": 5,
                        "retries": 3,
                    },
                }
            ],
        }

        # Save task definition
        aws_dir = self.project_root / "deployment" / "aws"
        aws_dir.mkdir(parents=True, exist_ok=True)

        with open(aws_dir / "ecs-task-definition.json", "w", encoding="utf-8") as f:
            json.dump(task_definition, f, indent=2)

        logger.info("AWS ECS task definition created")
        logger.info(
            "Deploy with: aws ecs register-task-definition --cli-input-json file://deployment/aws/ecs-task-definition.json"
        )
        return True

    def _get_aws_account_id(self) -> str:
        """Get AWS account ID."""
        try:
            result = subprocess.run(
                ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"],
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        except Exception:
            return "123456789012"  # Placeholder

    def build_and_push_docker_image(self, registry: str = None) -> bool:
        """Build and push Docker image to registry."""
        registry = registry or self.config.get("docker_registry", "your-registry.com")
        image_tag = f"{registry}/{self.config['app_name']}:{self.config['version']}"
        latest_tag = f"{registry}/{self.config['app_name']}:latest"

        logger.info(f"Building and pushing Docker image: {image_tag}")

        # Build image
        dockerfile_path = self.project_root / "deployment" / "docker" / "Dockerfile"
        build_cmd = ["docker", "build", "-t", image_tag, "-t", latest_tag, "-f", str(dockerfile_path), "."]

        result = subprocess.run(build_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"Docker build failed: {result.stderr}")
            return False

        # Push to registry
        for tag in [image_tag, latest_tag]:
            logger.info(f"Pushing {tag}...")
            push_result = subprocess.run(["docker", "push", tag], capture_output=True, text=True)
            if push_result.returncode != 0:
                logger.error(f"Docker push failed for {tag}: {push_result.stderr}")
                return False

        logger.info("Docker image pushed successfully!")
        return True

    def run_health_checks(self) -> bool:
        """Run health checks on deployed service."""
        logger.info("Running health checks...")

        health_endpoints: List[str] = []

        if self.target == "local":
            health_endpoints = ["http://localhost:7860/health"]
        elif self.target == "docker":
            health_endpoints = ["http://localhost:7860/health"]
        elif self.target == "k8s":
            # Get service endpoint
            try:
                result = subprocess.run(
                    [
                        "kubectl",
                        "get",
                        "service",
                        f"{self.config['app_name']}-service",
                        "-o",
                        "jsonpath={.status.loadBalancer.ingress[0].ip}",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                ip = result.stdout.strip()
                if ip:
                    health_endpoints = [f"http://{ip}/health"]
            except Exception:
                logger.warning("Could not get Kubernetes service IP")

        for endpoint in health_endpoints:
            try:
                import requests

                response = requests.get(endpoint, timeout=10)
                if response.status_code == 200:
                    logger.info(f"Health check passed: {endpoint}")
                    return True
                else:
                    logger.error(f"Health check failed: {endpoint} returned {response.status_code}")
            except Exception as e:
                logger.error(f"Health check failed: {endpoint} - {e}")

        # If no endpoints were available to check, don't fail the pipeline.
        return len(health_endpoints) == 0

    def setup_monitoring(self) -> bool:
        """Setup monitoring and alerting."""
        logger.info("Setting up monitoring...")

        monitoring_config = {
            "prometheus": {
                "scrape_configs": [
                    {
                        "job_name": self.config["app_name"],
                        "static_configs": [{"targets": [f"{self.config['app_name']}-service:7860"]}],
                    }
                ]
            },
            "grafana": {
                "dashboards": [
                    {
                        "name": f"{self.config['app_name']} Dashboard",
                        "panels": [
                            "Request Rate",
                            "Response Time",
                            "Error Rate",
                            "CPU Usage",
                            "Memory Usage",
                            "Audio Processing Time",
                        ],
                    }
                ]
            },
            "alerts": [
                {"name": "High Error Rate", "condition": "error_rate > 0.05", "action": "notify_slack"},
                {"name": "High Memory Usage", "condition": "memory_usage > 0.8", "action": "scale_up"},
            ],
        }

        monitoring_dir = self.project_root / "deployment" / "monitoring"
        monitoring_dir.mkdir(parents=True, exist_ok=True)

        with open(monitoring_dir / "monitoring-config.yaml", "w", encoding="utf-8") as f:
            yaml.dump(monitoring_config, f, default_flow_style=False)

        logger.info("Monitoring configuration created")
        return True

    def rollback_deployment(self, version: str = None) -> bool:
        """Rollback to previous deployment."""
        logger.info(f"Rolling back deployment to version: {version or 'previous'}")

        if self.target == "k8s":
            cmd = ["kubectl", "rollout", "undo", f"deployment/{self.config['app_name']}"]
            if version:
                cmd.extend(["--to-revision", version])

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                logger.info("Kubernetes rollback successful")
                return True
            else:
                logger.error(f"Kubernetes rollback failed: {result.stderr}")
                return False

        elif self.target == "aws":
            logger.info("AWS ECS rollback requires manual intervention via AWS Console or CLI")
            return True

        else:
            logger.warning(f"Rollback not implemented for target: {self.target}")
            return True

    def cleanup_old_deployments(self) -> bool:
        """Cleanup old deployments and resources."""
        logger.info("Cleaning up old deployments...")

        if self.target == "docker":
            # Remove old images
            try:
                subprocess.run(
                    ["docker", "image", "prune", "-f"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                logger.info("Cleaned up old Docker images")
            except Exception:
                logger.warning("Failed to cleanup Docker images")

        elif self.target == "k8s":
            # Cleanup completed jobs/pods
            try:
                subprocess.run(
                    ["kubectl", "delete", "pods", "--field-selector=status.phase=Succeeded"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                logger.info("Cleaned up completed Kubernetes pods")
            except Exception:
                logger.warning("Failed to cleanup Kubernetes pods")

        return True

    def generate_deployment_report(self) -> str:
        """Generate deployment report."""
        report = f"""
Enhanced Deforum Music Generator - Deployment Report
===================================================

Deployment Details:
- Target: {self.target}
- Environment: {self.environment}
- Version: {self.config['version']}
- Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S UTC')}

Configuration:
- App Name: {self.config['app_name']}
- Docker Registry: {self.config.get('docker_registry', 'N/A')}

Features Enabled:
"""
        for feature, enabled in self.config.get("features", {}).items():
            status = "✅" if enabled else "❌"
            report += f"- {feature}: {status}\n"

        report += f"""
Environment Resources:
- Replicas: {self.config['environments'][self.environment]['replicas']}
- CPU: {self.config['environments'][self.environment]['resources']['cpu']}
- Memory: {self.config['environments'][self.environment]['resources']['memory']}

Next Steps:
1. Verify deployment health: python deploy.py --health-check --target {self.target}
2. Setup monitoring: python deploy.py --setup-monitoring --target {self.target}
3. Configure alerts and notifications
4. Test with sample audio files

Support:
- Documentation: docs/deployment_guide.md
- Troubleshooting: docs/troubleshooting.md
- Health endpoint: /health
- Metrics endpoint: /metrics
"""

        return report


def main():
    parser = argparse.ArgumentParser(
        description="Enhanced Deforum Music Generator Deployment Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Setup new project
  python deploy.py --setup-project --target local

  # Local development deployment
  python deploy.py --target local --environment dev

  # Docker deployment
  python deploy.py --target docker --build-image

  # Kubernetes deployment
  python deploy.py --target k8s --environment prod --build-image --push-registry

  # AWS deployment
  python deploy.py --target aws --environment prod --build-image --push-registry

  # Health check
  python deploy.py --health-check --target k8s

  # Rollback
  python deploy.py --rollback --target k8s --version 3
        """,
    )

    # Main options
    parser.add_argument(
        "--target", choices=["local", "docker", "k8s", "aws", "azure"], default="local", help="Deployment target"
    )
    parser.add_argument(
        "--environment", choices=["dev", "staging", "prod"], default="dev", help="Environment"
    )

    # Actions
    parser.add_argument("--setup-project", action="store_true", help="Create complete project structure")
    parser.add_argument("--build-image", action="store_true", help="Build Docker image")
    parser.add_argument("--push-registry", action="store_true", help="Push to Docker registry")
    parser.add_argument("--deploy", action="store_true", default=True, help="Deploy to target (default action)")
    parser.add_argument("--health-check", action="store_true", help="Run health checks")
    parser.add_argument("--setup-monitoring", action="store_true", help="Setup monitoring")
    parser.add_argument("--rollback", action="store_true", help="Rollback deployment")
    parser.add_argument("--cleanup", action="store_true", help="Cleanup old deployments")

    # Configuration
    parser.add_argument("--registry", help="Docker registry URL")
    parser.add_argument("--version", help="Version for rollback")
    parser.add_argument("--config", help="Custom configuration file")

    args = parser.parse_args()

    # Initialize deployment manager
    deployment_manager = DeploymentManager(args.target, args.environment)

    # Load custom config if provided
    if args.config and Path(args.config).exists():
        with open(args.config, "r", encoding="utf-8") as f:
            custom_config = json.load(f)
            deployment_manager.config.update(custom_config)

    success = True

    try:
        # Setup project structure
        if args.setup_project:
            success &= deployment_manager.create_project_structure()

        # Build Docker image
        if args.build_image:
            if args.target in ["docker", "k8s", "aws", "azure"]:
                success &= deployment_manager.deploy_docker()

            if args.push_registry:
                success &= deployment_manager.build_and_push_docker_image(args.registry)

        # Deploy based on target
        if args.deploy and not any([args.setup_project, args.health_check, args.rollback, args.cleanup]):
            if args.target == "local":
                success &= deployment_manager.deploy_local()
            elif args.target == "docker":
                success &= deployment_manager.deploy_docker()
            elif args.target == "k8s":
                success &= deployment_manager.deploy_kubernetes()
            elif args.target == "aws":
                success &= deployment_manager.deploy_aws()

        # Health checks
        if args.health_check:
            success &= deployment_manager.run_health_checks()

        # Setup monitoring
        if args.setup_monitoring:
            success &= deployment_manager.setup_monitoring()

        # Rollback
        if args.rollback:
            success &= deployment_manager.rollback_deployment(args.version)

        # Cleanup
        if args.cleanup:
            success &= deployment_manager.cleanup_old_deployments()

        # Generate and save report
        report = deployment_manager.generate_deployment_report()
        with open("deployment-report.txt", "w", encoding="utf-8") as f:
            f.write(report)

        logger.info("\n" + "=" * 60)
        if success:
            logger.info("🎉 Deployment completed successfully!")
            logger.info("📊 Report saved to: deployment-report.txt")

            if args.target == "local":
                logger.info("🚀 Start the application:")
                if os.name == "nt":
                    logger.info("   start.bat")
                else:
                    logger.info("   ./start.sh")
            elif args.target == "docker":
                logger.info("🐳 Start with: docker-compose up -d")
            elif args.target == "k8s":
                logger.info("☸️  Check status: kubectl get pods")

        else:
            logger.error("❌ Deployment failed! Check logs above.")
            sys.exit(1)

    except KeyboardInterrupt:
        logger.info("\n⏹️  Deployment interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Deployment failed with error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
