"""
Advanced Features and Integrations for Enhanced Deforum Music Generator

New Features:
# Real-time preview generation
# Multi-track audio support
# Style transfer integration
# Advanced prompt templating system
# ComfyUI integration
# Video post-processing pipeline
# Web API server with authentication
# Cloud storage integration
# Advanced scheduling algorithms
# Interactive visualization dashboard
"""

import os
import json
import time
import asyncio
import hashlib
import logging
from typing import Dict, Any, List, Optional, Tuple, Union
from dataclasses import dataclass, asdict
from pathlib import Path
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import base64
import io
from enhanced_deforum_music_generator.config.config_system import Config

# Logging
logger = logging.getLogger(__name__)

# Web framework for API server
try:
    from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, BackgroundTasks
    from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import StreamingResponse, FileResponse
    import uvicorn
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

# Real-time visualization
try:
    import matplotlib.pyplot as plt
    import matplotlib.animation as animation
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

# Video processing
try:
    import cv2
    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False

# Cloud storage
try:
    import boto3
    from azure.storage.blob import BlobServiceClient
    HAS_CLOUD = True
except ImportError:
    HAS_CLOUD = False

# WebSocket for real-time updates
try:
    from fastapi import WebSocket
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False


@dataclass
class AdvancedGenerationConfig:
    """Configuration for advanced features."""
    
    # Multi-track support
    enable_multitrack: bool = False
    track_weights: Dict[str, float] = None
    
    # Style transfer
    enable_style_transfer: bool = False
    style_model_path: str = ""
    style_strength: float = 0.7
    
    # Real-time preview
    enable_preview: bool = False
    preview_fps: int = 2
    preview_duration: float = 30.0
    
    # Advanced scheduling
    scheduling_algorithm: str = "adaptive"  # "linear", "exponential", "adaptive"
    interpolation_method: str = "cubic"  # "linear", "cubic", "bezier"
    
    # Post-processing
    enable_postprocessing: bool = False
    upscale_factor: int = 1
    apply_stabilization: bool = False
    color_grading: str = "auto"  # "auto", "cinematic", "vibrant", "muted"
    
    # Cloud integration
    enable_cloud_storage: bool = False
    cloud_provider: str = "aws"  # "aws", "azure", "gcp"
    bucket_name: str = ""
    
    def __post_init__(self):
        if self.track_weights is None:
            self.track_weights = {"vocals": 0.6, "drums": 0.3, "bass": 0.1}


class MultiTrackAnalyzer:
    """Analyzes multiple audio tracks separately for richer control."""
    
    def __init__(self):
        self.track_separators = {}
        self._initialize_separators()
    
    def _initialize_separators(self):
        """Initialize audio separation models."""
        try:
            # Placeholder for audio separation (would use spleeter, demucs, etc.)
            # self.track_separators['spleeter'] = load_spleeter_model()
            pass
        except Exception as e:
            logger(f"Could not initialize track separators: {e}")
    
    def separate_tracks(self, audio_path: str) -> Dict[str, str]:
        """Separate audio into different instrument tracks."""
        # This would integrate with audio separation libraries
        # For now, return the original track as "mixed"
        return {"mixed": audio_path}
    
    def analyze_multitrack(self, audio_path: str, config: AdvancedGenerationConfig) -> Dict[str, Any]:
        """Analyze each track separately and combine results."""
        from enhanced_deforum_music_generator.core import AudioAnalyzer
        
        separated_tracks = self.separate_tracks(audio_path)
        analyzer = AudioAnalyzer()
        
        track_analyses = {}
        combined_features = {
            "tempo_bpm": 0.0,
            "energy_segments": [],
            "spectral_features": {},
            "beat_frames": []
        }
        
        for track_name, track_path in separated_tracks.items():
            analysis = analyzer.analyze(track_path, enable_lyrics=(track_name == "vocals"))
            track_analyses[track_name] = analysis
            
            # Weight and combine features
            weight = config.track_weights.get(track_name, 1.0 / len(separated_tracks))
            
            combined_features["tempo_bpm"] += analysis.tempo_bpm * weight
            
            if analysis.energy_segments:
                if not combined_features["energy_segments"]:
                    combined_features["energy_segments"] = [0.0] * len(analysis.energy_segments)
                
                for i, energy in enumerate(analysis.energy_segments):
                    if i < len(combined_features["energy_segments"]):
                        combined_features["energy_segments"][i] += energy * weight
            
            # Combine spectral features
            for feature, value in analysis.spectral_features.items():
                if feature not in combined_features["spectral_features"]:
                    combined_features["spectral_features"][feature] = 0.0
                combined_features["spectral_features"][feature] += value * weight
        
        return {
            "track_analyses": track_analyses,
            "combined_features": combined_features
        }


class AdvancedScheduleGenerator:
    """Generate sophisticated animation schedules with various algorithms."""
    
    def __init__(self):
        self.algorithms = {
            "linear": self._linear_interpolation,
            "cubic": self._cubic_interpolation,
            "exponential": self._exponential_interpolation,
            "adaptive": self._adaptive_interpolation,
            "bezier": self._bezier_interpolation
        }
    
    def generate_advanced_schedule(self, keyframes: List[Tuple[int, float]], 
                                 total_frames: int, algorithm: str = "adaptive") -> str:
        """Generate schedule using specified algorithm."""
        
        if algorithm not in self.algorithms:
            algorithm = "linear"
        
        interpolated_frames = self.algorithms[algorithm](keyframes, total_frames)
        
        # Format as Deforum schedule string
        schedule_parts = []
        for frame, value in interpolated_frames:
            schedule_parts.append(f"{frame}:({value:.4f})")
        
        return ", ".join(schedule_parts)
    
    def _linear_interpolation(self, keyframes: List[Tuple[int, float]], total_frames: int) -> List[Tuple[int, float]]:
        """Linear interpolation between keyframes."""
        if not keyframes:
            return [(0, 0.0)]
        
        keyframes = sorted(keyframes)
        result = []
        
        for i in range(len(keyframes) - 1):
            frame1, value1 = keyframes[i]
            frame2, value2 = keyframes[i + 1]
            
            # Add intermediate frames
            frame_diff = frame2 - frame1
            value_diff = value2 - value1
            
            for j in range(0, frame_diff + 1, max(1, frame_diff // 10)):
                if frame1 + j < total_frames:
                    progress = j / frame_diff if frame_diff > 0 else 0
                    interp_value = value1 + (value_diff * progress)
                    result.append((frame1 + j, interp_value))
        
        return result
    
    def _cubic_interpolation(self, keyframes: List[Tuple[int, float]], total_frames: int) -> List[Tuple[int, float]]:
        """Cubic spline interpolation for smoother curves."""
        try:
            import numpy as np
            from scipy.interpolate import CubicSpline
            
            if len(keyframes) < 2:
                return self._linear_interpolation(keyframes, total_frames)
            
            keyframes = sorted(keyframes)
            frames = [kf[0] for kf in keyframes]
            values = [kf[1] for kf in keyframes]
            
            cs = CubicSpline(frames, values)
            
            # Generate interpolated points
            result = []
            for frame in range(0, total_frames, max(1, total_frames // 100)):
                if frames[0] <= frame <= frames[-1]:
                    value = float(cs(frame))
                    result.append((frame, value))
            
            return result
            
        except ImportError:
            return self._linear_interpolation(keyframes, total_frames)
    
    def _exponential_interpolation(self, keyframes: List[Tuple[int, float]], total_frames: int) -> List[Tuple[int, float]]:
        """Exponential easing for dramatic transitions."""
        import math
        
        if not keyframes:
            return [(0, 0.0)]
        
        keyframes = sorted(keyframes)
        result = []
        
        for i in range(len(keyframes) - 1):
            frame1, value1 = keyframes[i]
            frame2, value2 = keyframes[i + 1]
            
            frame_diff = frame2 - frame1
            value_diff = value2 - value1
            
            for j in range(0, frame_diff + 1, max(1, frame_diff // 15)):
                if frame1 + j < total_frames:
                    progress = j / frame_diff if frame_diff > 0 else 0
                    # Exponential easing
                    eased_progress = 1 - math.exp(-5 * progress)
                    interp_value = value1 + (value_diff * eased_progress)
                    result.append((frame1 + j, interp_value))
        
        return result
    
    def _adaptive_interpolation(self, keyframes: List[Tuple[int, float]], total_frames: int) -> List[Tuple[int, float]]:
        """Adaptive interpolation based on value changes."""
        if len(keyframes) < 2:
            return self._linear_interpolation(keyframes, total_frames)
        
        keyframes = sorted(keyframes)
        result = []
        
        for i in range(len(keyframes) - 1):
            frame1, value1 = keyframes[i]
            frame2, value2 = keyframes[i + 1]
            
            frame_diff = frame2 - frame1
            value_diff = abs(value2 - value1)
            
            # More interpolation points for larger changes
            if value_diff > 0.5:
                step_size = max(1, frame_diff // 20)
            elif value_diff > 0.2:
                step_size = max(1, frame_diff // 10)
            else:
                step_size = max(1, frame_diff // 5)
            
            for j in range(0, frame_diff + 1, step_size):
                if frame1 + j < total_frames:
                    progress = j / frame_diff if frame_diff > 0 else 0
                    # Smooth step function
                    smooth_progress = progress * progress * (3 - 2 * progress)
                    interp_value = value1 + ((value2 - value1) * smooth_progress)
                    result.append((frame1 + j, interp_value))
        
        return result
    
    def _bezier_interpolation(self, keyframes: List[Tuple[int, float]], total_frames: int) -> List[Tuple[int, float]]:
        """Bezier curve interpolation for smooth artistic curves."""
        if len(keyframes) < 2:
            return self._linear_interpolation(keyframes, total_frames)
        
        keyframes = sorted(keyframes)
        result = []
        
        # Create Bezier curves between each pair of keyframes
        for i in range(len(keyframes) - 1):
            frame1, value1 = keyframes[i]
            frame2, value2 = keyframes[i + 1]
            
            # Create control points for smoother curves
            frame_diff = frame2 - frame1
            control1_frame = frame1 + frame_diff * 0.33
            control2_frame = frame1 + frame_diff * 0.67
            control1_value = value1 + (value2 - value1) * 0.2
            control2_value = value1 + (value2 - value1) * 0.8
            
            # Generate Bezier curve points
            for j in range(0, frame_diff + 1, max(1, frame_diff // 12)):
                if frame1 + j < total_frames:
                    t = j / frame_diff if frame_diff > 0 else 0
                    
                    # Cubic Bezier calculation
                    bezier_value = (
                        (1 - t)**3 * value1 +
                        3 * (1 - t)**2 * t * control1_value +
                        3 * (1 - t) * t**2 * control2_value +
                        t**3 * value2
                    )
                    
                    result.append((frame1 + j, bezier_value))
        
        return result


class StyleTransferEngine:
    """Applies artistic style transfer to generated content."""
    
    def __init__(self):
        self.style_models = {}
        self._load_style_models()
    
    def _load_style_models(self):
        """Load pre-trained style transfer models."""
        # Placeholder for style transfer model loading
        # Would integrate with neural style transfer models
        pass
    
    def apply_style_to_prompts(self, prompts: Dict[str, str], style_name: str, strength: float = 0.7) -> Dict[str, str]:
        """Apply style modifiers to existing prompts."""
        style_mappings = {
            "cinematic": {
                "modifiers": ["cinematic lighting", "film grain", "dramatic composition", "depth of field"],
                "color_palette": "desaturated with selective color pops"
            },
            "anime": {
                "modifiers": ["anime style", "cel shading", "vibrant colors", "detailed character design"],
                "color_palette": "bright and saturated colors"
            },
            "photorealistic": {
                "modifiers": ["photorealistic", "high detail", "natural lighting", "sharp focus"],
                "color_palette": "natural color grading"
            },
            "abstract": {
                "modifiers": ["abstract art", "geometric patterns", "fluid dynamics", "surreal"],
                "color_palette": "psychedelic color scheme"
            },
            "vintage": {
                "modifiers": ["vintage film", "retro aesthetic", "aged colors", "film texture"],
                "color_palette": "sepia and muted tones"
            }
        }
        
        if style_name not in style_mappings:
            return prompts
        
        style_config = style_mappings[style_name]
        styled_prompts = {}
        
        for frame, prompt in prompts.items():
            # Add style modifiers with controlled strength
            modifiers = style_config["modifiers"][:int(len(style_config["modifiers"]) * strength)]
            
            styled_prompt = prompt
            if modifiers:
                styled_prompt += ", " + ", ".join(modifiers)
            
            styled_prompts[frame] = styled_prompt
        
        return styled_prompts


class RealTimePreviewGenerator:
    """Generates real-time preview of the animation."""
    
    def __init__(self, config: AdvancedGenerationConfig):
        self.config = config
        self.preview_cache = {}
        self.is_generating = False
    
    async def generate_preview_frames(self, analysis, settings: Dict[str, Any]) -> List[bytes]:
        """Generate low-resolution preview frames."""
        if not HAS_MATPLOTLIB:
            return []
        
        preview_frames = []
        total_frames = int(analysis.duration * self.config.preview_fps)
        
        # Create visualization of audio features
        fig, axes = plt.subplots(2, 2, figsize=(8, 6))
        fig.patch.set_facecolor('black')
        
        for frame_idx in range(0, min(total_frames, 60)):  # Limit preview frames
            # Clear previous plots
            for ax in axes.flat:
                ax.clear()
                ax.set_facecolor('black')
            
            # Plot energy over time
            if analysis.energy_segments:
                segment_idx = int(frame_idx / total_frames * len(analysis.energy_segments))
                segment_idx = min(segment_idx, len(analysis.energy_segments) - 1)
                
                axes[0, 0].bar(range(len(analysis.energy_segments)), analysis.energy_segments, 
                              color=['red' if i == segment_idx else 'gray' for i in range(len(analysis.energy_segments))])
                axes[0, 0].set_title('Energy Segments', color='white')
                axes[0, 0].set_facecolor('black')
            
            # Plot beat tracking
            if analysis.beat_frames:
                current_time = frame_idx / self.config.preview_fps
                recent_beats = [b for b in analysis.beat_frames if current_time - 2 <= b <= current_time + 2]
                
                axes[0, 1].scatter(recent_beats, [1] * len(recent_beats), c='yellow', s=100)
                axes[0, 1].axvline(x=current_time, color='red', linestyle='--')
                axes[0, 1].set_xlim(current_time - 2, current_time + 2)
                axes[0, 1].set_title('Beat Tracking', color='white')
                axes[0, 1].set_facecolor('black')
            
            # Spectral features visualization
            brightness = analysis.spectral_features.get('brightness', 0.5)
            warmth = analysis.spectral_features.get('warmth', 0.5)
            
            axes[1, 0].bar(['Brightness', 'Warmth'], [brightness, warmth], color=['cyan', 'orange'])
            axes[1, 0].set_title('Spectral Features', color='white')
            axes[1, 0].set_facecolor('black')
            
            # Current frame info
            axes[1, 1].text(0.1, 0.8, f'Frame: {frame_idx}', color='white', fontsize=12)
            axes[1, 1].text(0.1, 0.6, f'Time: {frame_idx/self.config.preview_fps:.1f}s', color='white', fontsize=12)
            axes[1, 1].text(0.1, 0.4, f'BPM: {analysis.tempo_bpm:.1f}', color='white', fontsize=12)
            axes[1, 1].set_xlim(0, 1)
            axes[1, 1].set_ylim(0, 1)
            axes[1, 1].set_facecolor('black')
            axes[1, 1].axis('off')
            
            # Convert to bytes
            canvas = FigureCanvasAgg(fig)
            buf = io.BytesIO()
            canvas.logger_png(buf)
            preview_frames.append(buf.getvalue())
            buf.close()
        
        plt.close(fig)
        return preview_frames


class WebAPIServer:
    """FastAPI-based web server for the music generator."""
    
    def __init__(self):
        if not HAS_FASTAPI:
            raise ImportError("FastAPI is required for web server functionality")
        
        self.app = FastAPI(
            title="Enhanced Deforum Music Generator API",
            description="AI-powered audio-reactive video generation",
            version="2.0.0"
        )
        
        # CORS middleware
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],  # Configure appropriately for production
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        # Authentication
        self.security = HTTPBearer()
        self.api_keys = {"demo_key_123": "demo_user"}  # In production, use proper auth
        
        # Background tasks
        self.active_jobs = {}
        self.job_results = {}
        
        self._setup_routes()
    
    def _setup_routes(self):
        """Setup API routes."""
        
        @self.app.get("/health")
        async def health_check():
            return {"status": "healthy", "timestamp": time.time()}
        
        @self.app.post("/analyze")
        async def analyze_audio(
            file: UploadFile = File(...),
            enable_lyrics: bool = False,
            credentials: HTTPAuthorizationCredentials = Depends(self.security)
        ):
            # Verify API key
            if credentials.credentials not in self.api_keys:
                raise HTTPException(status_code=401, detail="Invalid API key")
            
            # Save uploaded file temporarily
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_file:
                content = await file.read()
                tmp_file.write(content)
                tmp_path = tmp_file.name
            
            try:
                # Analyze audio
                from enhanced_deforum_music_generator.core import AudioAnalyzer
                analyzer = AudioAnalyzer()
                analysis = analyzer.analyze(tmp_path, enable_lyrics=enable_lyrics)
                
                return {
                    "status": "success",
                    "analysis": analysis.to_dict(),
                    "filename": file.filename
                }
            
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
            
            finally:
                # Clean up temp file
                try:
                    os.unlink(tmp_path)
                except:
                    pass
        
        @self.app.post("/generate")
        async def generate_deforum_settings(
            background_tasks: BackgroundTasks,
            file: UploadFile = File(...),
            settings: str = '{}',  # JSON string of generation settings
            credentials: HTTPAuthorizationCredentials = Depends(self.security)
        ):
            if credentials.credentials not in self.api_keys:
                raise HTTPException(status_code=401, detail="Invalid API key")
            
            # Parse settings
            try:
                user_settings = json.loads(settings)
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid settings JSON")
            
            # Create job ID
            job_id = hashlib.md5(f"{file.filename}_{time.time()}".encode()).hexdigest()[:16]
            
            # Start background job
            background_tasks.add_task(self._process_generation_job, job_id, file, user_settings)
            
            return {
                "status": "accepted",
                "job_id": job_id,
                "message": "Generation started in background"
            }
        
        @self.app.get("/job/{job_id}")
        async def get_job_status(job_id: str):
            if job_id in self.job_results:
                return self.job_results[job_id]
            elif job_id in self.active_jobs:
                return {"status": "processing", "job_id": job_id}
            else:
                raise HTTPException(status_code=404, detail="Job not found")
        
        @self.app.get("/download/{job_id}")
        async def download_result(job_id: str):
            if job_id not in self.job_results:
                raise HTTPException(status_code=404, detail="Job not found")
            
            result = self.job_results[job_id]
            if result["status"] != "completed":
                raise HTTPException(status_code=400, detail="Job not completed")
            
            file_path = result.get("file_path")
            if not file_path or not os.path.exists(file_path):
                raise HTTPException(status_code=404, detail="Result file not found")
            
            return FileResponse(
                file_path,
                media_type='application/zip',
                filename=f"deforum_settings_{job_id}.zip"
            )
        
        if HAS_WEBSOCKETS:
            @self.app.websocket("/ws/preview/{job_id}")
            async def websocket_preview(websocket: WebSocket, job_id: str):
                await websocket.accept()
                
                # Send real-time preview updates
                try:
                    while job_id in self.active_jobs:
                        # Get current progress
                        job_data = self.active_jobs.get(job_id, {})
                        progress = job_data.get("progress", 0)
                        
                        await websocket.send_json({
                            "job_id": job_id,
                            "progress": progress,
                            "timestamp": time.time()
                        })
                        
                        await asyncio.sleep(1.0)
                        
                except Exception:
                    pass
                finally:
                    await websocket.close()
    
    async def _process_generation_job(self, job_id: str, file: UploadFile, settings: Dict[str, Any]):
        """Process generation job in background."""
        self.active_jobs[job_id] = {"progress": 0, "status": "starting"}
        
        try:
            # Save uploaded file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_file:
                content = await file.read()
                tmp_file.write(content)
                tmp_path = tmp_file.name
            
            # Update progress
            self.active_jobs[job_id]["progress"] = 25
            
            # Generate package
            from enhanced_deforum_music_generator.core import create_package
            
            output_dir = tempfile.mkdtemp()
            package_path = create_package(
                tmp_path,
                output_dir=output_dir,
                **settings
            )
            
            # Update progress
            self.active_jobs[job_id]["progress"] = 100
            
            # Store result
            self.job_results[job_id] = {
                "status": "completed",
                "job_id": job_id,
                "file_path": package_path,
                "timestamp": time.time()
            }
            
        except Exception as e:
            self.job_results[job_id] = {
                "status": "failed",
                "job_id": job_id,
                "error": str(e),
                "timestamp": time.time()
            }
        
        finally:
            # Clean up
            if job_id in self.active_jobs:
                del self.active_jobs[job_id]
            
            try:
                os.unlink(tmp_path)
            except:
                pass
    
    def run(self, host: str = "0.0.0.0", port: int = 8000):
        """Run the web server."""
        uvicorn.run(self.app, host=host, port=port, log_level="info")


class CloudStorageManager:
    """Manages cloud storage integration."""
    
    def __init__(self, provider: str = "aws", **credentials):
        self.provider = provider
        self.credentials = credentials
        self.client = None
        self._initialize_client()
    
    def _initialize_client(self):
        """Initialize cloud storage client."""
        if not HAS_CLOUD:
            logger("Cloud storage libraries not available")
            return
        
        try:
            if self.provider == "aws":
                self.client = boto3.client(
                    's3',
                    aws_access_key_id=self.credentials.get('access_key'),
                    aws_secret_access_key=self.credentials.get('secret_key')
                )
            elif self.provider == "azure":
                self.client = BlobServiceClient(
                    account_url=self.credentials.get('account_url'),
                    credential=self.credentials.get('credential')
                )
        except Exception as e:
            logger(f"Failed to initialize cloud storage client: {e}")
    
    def upload_file(self, local_path: str, remote_path: str, bucket: str) -> bool:
        """Upload file to cloud storage."""
        if not self.client:
            return False
        
        try:
            if self.provider == "aws":
                self.client.upload_file(local_path, bucket, remote_path)
            elif self.provider == "azure":
                blob_client = self.client.get_blob_client(
                    container=bucket, 
                    blob=remote_path
                )
                with open(local_path, 'rb') as data:
                    blob_client.upload_blob(data, overwrite=True)
            
            return True
        except Exception as e:
            logger(f"Upload failed: {e}")
            return False
    
    def download_file(self, remote_path: str, local_path: str, bucket: str) -> bool:
        """Download file from cloud storage."""
        if not self.client:
            return False
        
        try:
            if self.provider == "aws":
                self.client.download_file(bucket, remote_path, local_path)
            elif self.provider == "azure":
                blob_client = self.client.get_blob_client(
                    container=bucket,
                    blob=remote_path
                )
                with open(local_path, 'wb') as f:
                    f.write(blob_client.download_blob().readall())
            
            return True
        except Exception as e:
            logger(f"Download failed: {e}")
            return False


class ComfyUIIntegration:
    """Integration with ComfyUI for advanced workflow support."""
    
    def __init__(self, comfyui_api_url: str = "http://localhost:8188"):
        self.api_url = comfyui_api_url
        self.workflow_templates = {}
        self._load_workflow_templates()
    
    def _load_workflow_templates(self):
        """Load ComfyUI workflow templates."""
        # Deforum-compatible ComfyUI workflow template
        self.workflow_templates["deforum_basic"] = {
            "1": {
                "inputs": {
                    "text": "PLACEHOLDER_PROMPT",
                    "clip": ["4", 1]
                },
                "class_type": "CLIPTextEncode"
            },
            "2": {
                "inputs": {
                    "text": "PLACEHOLDER_NEGATIVE",
                    "clip": ["4", 1]
                },
                "class_type": "CLIPTextEncode"
            },
            "3": {
                "inputs": {
                    "seed": "PLACEHOLDER_SEED",
                    "steps": "PLACEHOLDER_STEPS",
                    "cfg": "PLACEHOLDER_CFG",
                    "sampler_name": "PLACEHOLDER_SAMPLER",
                    "scheduler": "normal",
                    "positive": ["1", 0],
                    "negative": ["2", 0],
                    "latent_image": ["5", 0]
                },
                "class_type": "KSampler"
            },
            "4": {
                "inputs": {
                    "ckpt_name": "PLACEHOLDER_MODEL"
                },
                "class_type": "CheckpointLoaderSimple"
            },
            "5": {
                "inputs": {
                    "width": "PLACEHOLDER_WIDTH",
                    "height": "PLACEHOLDER_HEIGHT",
                    "batch_size": 1
                },
                "class_type": "EmptyLatentImage"
            },
            "6": {
                "inputs": {
                    "samples": ["3", 0],
                    "vae": ["4", 2]
                },
                "class_type": "VAEDecode"
            },
            "7": {
                "inputs": {
                    "filename_prefix": "deforum_frame_",
                    "images": ["6", 0]
                },
                "class_type": "SaveImage"
            }
        }
    
    def convert_deforum_to_comfyui(self, deforum_settings: Dict[str, Any]) -> Dict[str, Any]:
        """Convert Deforum settings to ComfyUI workflow."""
        workflow = self.workflow_templates["deforum_basic"].copy()
        
        # Map Deforum parameters to ComfyUI
        prompts = deforum_settings.get("prompts", {})
        main_prompt = list(prompts.values())[0] if prompts else "cinematic masterpiece"
        
        # Replace placeholders
        workflow_str = json.dumps(workflow)
        workflow_str = workflow_str.replace("PLACEHOLDER_PROMPT", main_prompt)
        workflow_str = workflow_str.replace("PLACEHOLDER_NEGATIVE", 
                                          deforum_settings.get("negative_prompts", {}).get("0", "low quality"))
        workflow_str = workflow_str.replace("PLACEHOLDER_SEED", str(deforum_settings.get("seed", -1)))
        workflow_str = workflow_str.replace("PLACEHOLDER_STEPS", str(deforum_settings.get("steps", 25)))
        workflow_str = workflow_str.replace("PLACEHOLDER_CFG", str(deforum_settings.get("scale", 7.0)))
        workflow_str = workflow_str.replace("PLACEHOLDER_SAMPLER", deforum_settings.get("sampler", "euler"))
        workflow_str = workflow_str.replace("PLACEHOLDER_WIDTH", str(deforum_settings.get("W", 1024)))
        workflow_str = workflow_str.replace("PLACEHOLDER_HEIGHT", str(deforum_settings.get("H", 576)))
        workflow_str = workflow_str.replace("PLACEHOLDER_MODEL", "sd_xl_base_1.0.safetensors")
        
        return json.loads(workflow_str)
    
    async def queue_workflow(self, workflow: Dict[str, Any]) -> str:
        """Queue workflow in ComfyUI."""
        try:
            import aiohttp
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/prompt",
                    json={"prompt": workflow}
                ) as response:
                    result = await response.json()
                    return result.get("prompt_id", "")
        
        except Exception as e:
            logger(f"Failed to queue ComfyUI workflow: {e}")
            return ""


# Example usage and integration
def create_advanced_generator(config: AdvancedGenerationConfig):
    """Create generator with advanced features enabled."""
    
    components = {}
    
    # Multi-track analysis
    if config.enable_multitrack:
        components['multitrack'] = MultiTrackAnalyzer()
    
    # Style transfer
    if config.enable_style_transfer:
        components['style_transfer'] = StyleTransferEngine()
    
    # Advanced scheduling
    components['scheduler'] = AdvancedScheduleGenerator()
    
    # Real-time preview
    if config.enable_preview:
        components['preview'] = RealTimePreviewGenerator(config)
    
    # Cloud storage
    if config.enable_cloud_storage:
        components['cloud'] = CloudStorageManager(config.cloud_provider)
    
    return components


if __name__ == "__main__":
    # Example: Start web API server
    if HAS_FASTAPI:
        server = WebAPIServer()
        logger("Starting Enhanced Deforum Music Generator API server...")
        logger("Access at: http://localhost:8000/docs")
        server.run(port=8000)
    else:
        logger("FastAPI not available. Install with: pip install fastapi uvicorn")
        
        # Example: Test advanced features
        config = AdvancedGenerationConfig(
            enable_multitrack=True,
            enable_style_transfer=True,
            enable_preview=True
        )
        
        advanced_components = create_advanced_generator(config)
        logger(f"Advanced components created: {list(advanced_components.keys())}")
