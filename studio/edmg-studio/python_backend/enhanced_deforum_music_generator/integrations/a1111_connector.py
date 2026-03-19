"""
A1111 Integration Module
Handles integration with Automatic1111 WebUI and Deforum extension
"""

from typing import List, Optional, Dict, Any

# A1111 Integration imports with fallback handling
try:
    import modules.scripts as scripts
    import modules.shared as shared
    import modules.processing as processing
    import modules.sd_samplers as samplers
    import modules.sd_models as sd_models
    from modules import script_callbacks, devices
    A1111_AVAILABLE = True
except ImportError:
    A1111_AVAILABLE = False

class A1111Connector:
    """Manages connection and integration with A1111 WebUI"""
    
    def __init__(self, config):
        self.config = config
        self.deforum_available = False
        self.deforum_script = None
        self.models = []
        self.samplers = []
        
        if A1111_AVAILABLE and config.enable_integration:
            self._initialize_integration()
    
    def _initialize_integration(self):
        """Initialize A1111 integration"""
        try:
            # Check for Deforum extension
            if self.config.enable_deforum_detection:
                self._detect_deforum()
            
            # Get available models
            if self.config.auto_detect_models:
                self._load_models()
            
            # Get available samplers  
            if self.config.auto_detect_samplers:
                self._load_samplers()
                
            print("A1111 integration initialized successfully")
            
        except Exception as e:
            print(f"A1111 integration initialization failed: {e}")
            self._set_fallback_values()
    
    def _detect_deforum(self):
        """Detect Deforum extension"""
        # Method 1: Check in txt2img scripts
        if hasattr(scripts, 'scripts_txt2img'):
            for script in scripts.scripts_txt2img.alwayson_scripts:
                if hasattr(script, 'title'):
                    title_lower = script.title().lower()
                    if any(name in title_lower for name in self.config.deforum_extension_names):
                        self.deforum_script = script
                        self.deforum_available = True
                        print(f"Deforum extension detected: {script.title()}")
                        return
        
        # Method 2: Check for deforum helpers
        try:
            import deforum_helpers.args as deforum_args
            self.deforum_available = True
            print("Deforum helpers detected")
        except ImportError:
            pass
        
        if not self.deforum_available:
            print("Deforum extension not detected - settings will be generic")
    
    def _load_models(self):
        """Load available models from A1111"""
        try:
            if hasattr(sd_models, 'checkpoints_list') and sd_models.checkpoints_list:
                self.models = [
                    checkpoint.model_name 
                    for checkpoint in sd_models.checkpoints_list.values()
                ]
            elif hasattr(sd_models, 'checkpoint_tiles') and sd_models.checkpoint_tiles():
                self.models = list(sd_models.checkpoint_tiles())
            else:
                self.models = [self.config.default_model]
                
        except Exception as e:
            print(f"Failed to load models: {e}")
            self.models = [self.config.default_model]
    
    def _load_samplers(self):
        """Load available samplers from A1111"""
        try:
            if hasattr(samplers, 'all_samplers') and samplers.all_samplers:
                self.samplers = [sampler.name for sampler in samplers.all_samplers]
            else:
                self.samplers = ["Euler a", "DPM++ 2M Karras", "DDIM", "PLMS", "LMS"]
                
        except Exception as e:
            print(f"Failed to load samplers: {e}")
            self.samplers = ["Euler a", "DPM++ 2M Karras", "DDIM"]
    
    def _set_fallback_values(self):
        """Set fallback values when integration fails"""
        self.models = [self.config.default_model]
        self.samplers = [self.config.default_sampler, "DPM++ 2M Karras", "DDIM"]
    
    def get_models(self) -> List[str]:
        """Get list of available models"""
        return self.models if self.models else [self.config.default_model]
    
    def get_samplers(self) -> List[str]:
        """Get list of available samplers"""
        return self.samplers if self.samplers else [self.config.default_sampler]
    
    def is_deforum_available(self) -> bool:
        """Check if Deforum extension is available"""
        return self.deforum_available
    
    def get_integration_status(self) -> Dict[str, Any]:
        """Get current integration status"""
        return {
            "a1111_available": A1111_AVAILABLE,
            "deforum_available": self.deforum_available,
            "models_loaded": len(self.models),
            "samplers_loaded": len(self.samplers),
            "integration_enabled": self.config.enable_integration
        }
    
    def validate_deforum_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and clean settings for Deforum compatibility"""
        # Ensure required Deforum fields exist
        deforum_defaults = {
            "animation_mode": "3D",
            "border": "replicate",
            "max_frames": 240,
            
            # 3D settings
            "use_depth_warping": True,
            "midas_weight": 0.3,
            "near_plane": "200",
            "far_plane": "10000",
            "fov": "70",
            "padding_mode": "border",
            "sampling_mode": "bicubic",
            
            # Coherence
            "color_coherence": "LAB",
            "diffusion_cadence": 2,
            
            # Video output
            "output_format": "mp4",
            "fps": 24,
            
            # Batch settings
            "batch_name": "Enhanced_Deforum",
            "filename_format": "{timestring}_{index:05d}_{seed}",
            "seed_behavior": "iter",
            "make_grid": False,
            
            # Save settings
            "save_settings": True,
            "save_samples": True,
            "display_samples": True
        }
        
        # Merge with defaults
        validated_settings = {**deforum_defaults, **settings}
        
        # Validate specific fields
        validated_settings = self._validate_numeric_fields(validated_settings)
        validated_settings = self._validate_schedule_fields(validated_settings)
        
        return validated_settings
    
    def _validate_numeric_fields(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Validate numeric fields are within reasonable ranges"""
        numeric_validations = {
            "max_frames": (1, 10000),
            "fps": (1, 60),
            "steps": (1, 150),
            "scale": (1.0, 30.0),  # CFG scale
            "midas_weight": (0.0, 1.0),
            "diffusion_cadence": (1, 10)
        }
        
        for field, (min_val, max_val) in numeric_validations.items():
            if field in settings:
                try:
                    value = float(settings[field])
                    settings[field] = max(min_val, min(max_val, value))
                except (ValueError, TypeError):
                    print(f"Invalid {field} value, using default")
                    # Keep existing value or use a reasonable default
        
        return settings
    
    def _validate_schedule_fields(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Validate schedule string formats"""
        schedule_fields = [
            "zoom", "angle", "translation_x", "translation_y", "translation_z",
            "rotation_3d_x", "rotation_3d_y", "rotation_3d_z",
            "strength_schedule", "noise_schedule", "contrast_schedule"
        ]
        
        for field in schedule_fields:
            if field in settings:
                schedule = settings[field]
                if not self._is_valid_schedule_format(schedule):
                    print(f"Invalid schedule format for {field}, using default")
                    settings[field] = "0:(1.0)" if "zoom" in field else "0:(0)"
        
        return settings
    
    def _is_valid_schedule_format(self, schedule: str) -> bool:
        """Check if schedule string is in valid Deforum format"""
        if not schedule or not isinstance(schedule, str):
            return False
        
        try:
            # Basic format check: should contain frame:value pairs
            parts = schedule.split(',')
            for part in parts:
                part = part.strip()
                if ':' not in part or '(' not in part or ')' not in part:
                    return False
            return True
        except:
            return False

# ---------------------------------------------------------------------
# Lightweight HTTP client (used by unit tests and standalone mode)
# ---------------------------------------------------------------------
from dataclasses import dataclass
from typing import Any as _Any, Dict as _Dict, Optional as _Optional
import json as _json
import urllib.request as _urlreq
import urllib.parse as _urlparse

@dataclass
class Txt2ImgResult:
    images_b64: list
    parameters: _Dict[str, _Any]
    info: str

class A1111Connector(A1111Connector):  # type: ignore[misc]
    """Augment the main connector with a small REST client surface.

    The original connector supports embedded A1111 execution when imported
    inside the WebUI environment. For tests and standalone use, we provide
    `_get`, `_post`, `ping`, and `txt2img` methods.
    """

    def _url(self, path: str) -> str:
        base = getattr(self.config, "base_url", None) or f"http://{self.config.host}:{self.config.port}"
        if not path.startswith("/"):
            path = "/" + path
        return base.rstrip("/") + path

    def _get(self, path: str, params: _Optional[_Dict[str, _Any]] = None) -> _Dict[str, _Any]:
        url = self._url(path)
        if params:
            url += "?" + _urlparse.urlencode(params)
        req = _urlreq.Request(url, method="GET")
        with _urlreq.urlopen(req, timeout=getattr(self.config, "timeout", 120)) as r:
            data = r.read().decode("utf-8")
        return _json.loads(data) if data else {}

    def _post(self, path: str, payload: _Dict[str, _Any]) -> _Dict[str, _Any]:
        url = self._url(path)
        body = _json.dumps(payload).encode("utf-8")
        req = _urlreq.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
        with _urlreq.urlopen(req, timeout=getattr(self.config, "timeout", 120)) as r:
            data = r.read().decode("utf-8")
        return _json.loads(data) if data else {}

    def ping(self) -> bool:
        try:
            _ = self._get("/sdapi/v1/progress")
            return True
        except Exception:
            return False

    def txt2img(self, prompt: str, **kwargs) -> Txt2ImgResult:
        payload = {
            "prompt": prompt,
            "steps": int(kwargs.get("steps", 20)),
            "width": int(kwargs.get("width", 512)),
            "height": int(kwargs.get("height", 512)),
        }
        payload.update({k: v for k, v in kwargs.items() if k not in payload})
        resp = self._post("/sdapi/v1/txt2img", payload)
        return Txt2ImgResult(
            images_b64=resp.get("images", []),
            parameters=resp.get("parameters", {}),
            info=resp.get("info", ""),
        )
